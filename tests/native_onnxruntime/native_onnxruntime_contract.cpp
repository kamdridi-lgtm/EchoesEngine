#include "EchoesEngine/neural/NeuralScheduler.h"
#include "EchoesEngine/neural/OnnxRuntimeSession.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace fs = std::filesystem;
using echoes::neural::ModelEvidenceStatus;
using echoes::neural::NeuralScheduleRequest;
using echoes::neural::NeuralScheduler;
using echoes::neural::OnnxModelDescriptor;
using echoes::neural::OnnxModelManager;
using echoes::neural::OnnxRuntimeSession;
using echoes::neural::Sha256;

static void require(bool condition, const std::string& message) {
    if (!condition) throw std::runtime_error(message);
}

static bool near(float left, float right, float tolerance = 1.0e-6f) {
    return std::abs(left - right) <= tolerance;
}

static std::string hashFloats(const std::vector<float>& values) {
    Sha256 hash;
    hash.update(
        reinterpret_cast<const std::uint8_t*>(values.data()),
        values.size() * sizeof(float));
    return hash.finalize();
}

static void writeProof(const fs::path& outputPath,
                       const std::string& modelSha,
                       std::uintmax_t modelSize,
                       const std::vector<float>& output,
                       const std::string& outputSha,
                       double firstMs,
                       double secondMs,
                       const std::string& selectedModel,
                       bool executionAuthorized) {
    std::ofstream proof(outputPath);
    if (!proof) throw std::runtime_error("unable to create proof JSON");
    proof << std::fixed << std::setprecision(6);
    proof << "{\n"
          << "  \"schema\": \"echoes.onnx-runtime-inference-proof.v1\",\n"
          << "  \"status\": \"PASS\",\n"
          << "  \"model\": {\n"
          << "    \"sha256\": \"" << modelSha << "\",\n"
          << "    \"sizeBytes\": " << modelSize << ",\n"
          << "    \"productionModel\": false,\n"
          << "    \"voiceConversionModel\": false,\n"
          << "    \"videoModel\": false\n"
          << "  },\n"
          << "  \"runtime\": {\n"
          << "    \"provider\": \"CPUExecutionProvider\",\n"
          << "    \"gpuAccelerated\": false,\n"
          << "    \"tensorRtUsed\": false\n"
          << "  },\n"
          << "  \"input\": [0.0, 1.0, -2.0, 3.5],\n"
          << "  \"expectedOutput\": [1.0, 3.0, -3.0, 8.0],\n"
          << "  \"actualOutput\": [";
    for (std::size_t index = 0; index < output.size(); ++index) {
        if (index != 0U) proof << ", ";
        proof << output[index];
    }
    proof << "],\n"
          << "  \"outputSha256\": \"" << outputSha << "\",\n"
          << "  \"repeatDeterministic\": true,\n"
          << "  \"outputFinite\": true,\n"
          << "  \"firstInferenceMs\": " << firstMs << ",\n"
          << "  \"secondInferenceMs\": " << secondMs << ",\n"
          << "  \"scheduler\": {\n"
          << "    \"status\": \"PLANNED\",\n"
          << "    \"selectedModelId\": \"" << selectedModel << "\",\n"
          << "    \"executionAuthorized\": " << (executionAuthorized ? "true" : "false") << ",\n"
          << "    \"requiresOperatorApproval\": true\n"
          << "  },\n"
          << "  \"truthBoundary\": {\n"
          << "    \"cpuReferenceInferenceProven\": true,\n"
          << "    \"productionModelProvisioned\": false,\n"
          << "    \"voiceConversionProven\": false,\n"
          << "    \"gpuInferenceProven\": false,\n"
          << "    \"tensorRtInferenceProven\": false\n"
          << "  }\n"
          << "}\n";
}

int main(int argc, char** argv) {
    if (argc != 5) {
        std::cerr << "usage: echoes_onnxruntime_contract <model.onnx> <sha256> <size> <proof.json>\n";
        return 2;
    }

    const fs::path modelPath = fs::absolute(argv[1]);
    const std::string expectedSha = argv[2];
    const auto expectedSize = static_cast<std::uintmax_t>(std::stoull(argv[3]));
    const fs::path proofPath = argv[4];

    OnnxModelDescriptor descriptor;
    descriptor.id = "echoes-reference-transform-v1";
    descriptor.purpose = "reference_inference";
    descriptor.relativePath = modelPath.filename();
    descriptor.expectedSha256 = expectedSha;
    descriptor.expectedSizeBytes = expectedSize;
    descriptor.minimumVramGiB = 0.0;
    descriptor.qualityScore = 100;
    descriptor.maxConcurrency = 1;
    descriptor.commercialUseAllowed = false;
    descriptor.providers = {"cpu"};
    descriptor.precisions = {"fp32"};
    descriptor.capabilities = {"deterministicTransform", "referenceInference"};

    OnnxModelManager manager(modelPath.parent_path());
    auto evidence = manager.inspect(descriptor);
    require(evidence.status == ModelEvidenceStatus::PASS, "reference model integrity did not pass");
    require(evidence.actualSha256 == expectedSha, "reference model SHA-256 drifted");
    require(evidence.actualSizeBytes == expectedSize, "reference model size drifted");
    require(!evidence.networkRequested && !evidence.executableLoaded,
            "integrity manager must remain offline and non-executing");

    OnnxRuntimeSession session;
    require(session.load(evidence.resolvedPath), "ONNX Runtime could not load the reference model");
    require(session.isLoaded(), "session did not report loaded state");
    require(session.declaredInputShape() == std::vector<std::int64_t>({1, 4}),
            "unexpected declared input shape");
    require(session.declaredOutputShape() == std::vector<std::int64_t>({1, 4}),
            "unexpected declared output shape");

    const std::vector<float> input = {0.0f, 1.0f, -2.0f, 3.5f};
    const std::vector<float> expected = {1.0f, 3.0f, -3.0f, 8.0f};
    const std::vector<std::int64_t> shape = {1, 4};
    const auto first = session.run(input, shape);
    const auto second = session.run(input, shape);
    require(first.status == "PASS" && second.status == "PASS", "reference inference did not pass twice");
    require(first.modelLoaded && first.inferenceExecuted && first.outputFinite,
            "first inference evidence is incomplete");
    require(first.provider == "CPUExecutionProvider" && !first.gpuAccelerated && !first.tensorRtUsed,
            "reference proof must remain CPU-only");
    require(first.outputShape == shape && second.outputShape == shape, "output shape drifted");
    require(first.output.size() == expected.size() && second.output.size() == expected.size(),
            "output element count drifted");
    for (std::size_t index = 0; index < expected.size(); ++index) {
        require(near(first.output[index], expected[index]), "first inference output mismatch");
        require(near(second.output[index], expected[index]), "second inference output mismatch");
        require(near(first.output[index], second.output[index]), "repeat inference is not deterministic");
    }

    const auto invalidShape = session.run(input, {4});
    require(invalidShape.status == "BLOCKED", "rank-mismatched input must be blocked");
    require(std::find(
        invalidShape.blockers.begin(),
        invalidShape.blockers.end(),
        "INPUT_SHAPE_NOT_COMPATIBLE") != invalidShape.blockers.end(),
        "shape blocker missing");

    NeuralScheduleRequest request;
    request.jobId = "reference-inference-job";
    request.purpose = "reference_inference";
    request.requiredCapabilities = {"deterministicTransform"};
    request.providerPreference = {"cpu"};
    request.precisionPreference = {"fp32"};
    request.inferenceProofModelIds = {descriptor.id};
    request.commercialUse = false;
    request.availableVramGiB = 0.0;
    request.reserveVramGiB = 0.0;
    request.minimumQuality = 100;
    request.maximumConcurrency = 1;

    NeuralScheduler scheduler;
    const auto plan = scheduler.plan(request, {evidence});
    require(plan.status == "PLANNED", "inference-proven model was not schedulable");
    require(plan.selectedModelId == descriptor.id, "scheduler selected the wrong reference model");
    require(!plan.executionAuthorized && plan.requiresOperatorApproval,
            "scheduler must remain read-only after inference proof");

    auto unprovenRequest = request;
    unprovenRequest.inferenceProofModelIds.clear();
    const auto blockedPlan = scheduler.plan(unprovenRequest, {evidence});
    require(blockedPlan.status == "BLOCKED", "integrity-only model must not be schedulable");
    require(std::find(
        blockedPlan.blockers.begin(),
        blockedPlan.blockers.end(),
        "MODEL_INFERENCE_NOT_PROVEN") != blockedPlan.blockers.end(),
        "unproven-inference blocker missing");

    const auto outputSha = hashFloats(first.output);
    writeProof(
        proofPath,
        expectedSha,
        expectedSize,
        first.output,
        outputSha,
        first.inferenceMs,
        second.inferenceMs,
        plan.selectedModelId,
        plan.executionAuthorized);

    std::cout << "EchoesOnnxRuntimeInference PASS"
              << " model-loaded=true"
              << " inference-executed=true"
              << " output=1,3,-3,8"
              << " repeat=deterministic"
              << " shape-mismatch=blocked"
              << " integrity-only=blocked"
              << " provider=cpu"
              << " gpu=false"
              << " tensorrt=false"
              << " scheduler=planned"
              << " execution=not-authorized"
              << "\n";
    return 0;
}
