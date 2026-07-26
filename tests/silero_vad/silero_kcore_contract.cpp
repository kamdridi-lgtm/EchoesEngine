#include "EchoesEngine/neural/NeuralScheduler.h"

#include <algorithm>
#include <cstdint>
#include <filesystem>
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

static void require(bool condition, const std::string& message) {
    if (!condition) throw std::runtime_error(message);
}

static bool contains(const std::vector<std::string>& values, const std::string& value) {
    return std::find(values.begin(), values.end(), value) != values.end();
}

int main(int argc, char** argv) {
    if (argc != 4) {
        std::cerr << "usage: echoes_silero_kcore_contract <model.onnx> <sha256> <size>\n";
        return 2;
    }

    const fs::path modelPath = fs::absolute(argv[1]);
    const std::string expectedSha = argv[2];
    const auto expectedSize = static_cast<std::uintmax_t>(std::stoull(argv[3]));

    OnnxModelDescriptor descriptor;
    descriptor.id = "silero-vad-6.2.1";
    descriptor.purpose = "voice_activity_detection";
    descriptor.relativePath = modelPath.filename();
    descriptor.expectedSha256 = expectedSha;
    descriptor.expectedSizeBytes = expectedSize;
    descriptor.minimumVramGiB = 0.0;
    descriptor.qualityScore = 94;
    descriptor.maxConcurrency = 4;
    descriptor.commercialUseAllowed = true;
    descriptor.providers = {"cpu"};
    descriptor.precisions = {"fp32"};
    descriptor.capabilities = {
        "voiceActivityDetection",
        "voiceActivityProbability",
        "speechTimestamping",
        "streamingInference",
        "recurrentState"
    };

    OnnxModelManager manager(modelPath.parent_path());
    const auto evidence = manager.inspect(descriptor);
    require(evidence.status == ModelEvidenceStatus::PASS, "Silero integrity evidence did not pass");
    require(evidence.actualSha256 == expectedSha, "Silero SHA-256 drifted");
    require(evidence.actualSizeBytes == expectedSize, "Silero size drifted");
    require(!evidence.networkRequested && !evidence.executableLoaded,
            "Integrity inspection must remain offline and non-executing");

    NeuralScheduleRequest request;
    request.jobId = "silero-vad-timestamp-production-job";
    request.purpose = "voice_activity_detection";
    request.requiredCapabilities = {
        "voiceActivityDetection",
        "speechTimestamping",
        "streamingInference",
        "recurrentState"
    };
    request.providerPreference = {"cpu"};
    request.precisionPreference = {"fp32"};
    request.inferenceProofModelIds = {descriptor.id};
    request.commercialUse = true;
    request.availableVramGiB = 0.0;
    request.reserveVramGiB = 0.0;
    request.minimumQuality = 94;
    request.maximumConcurrency = 4;

    NeuralScheduler scheduler;
    const auto plan = scheduler.plan(request, {evidence});
    require(plan.status == "PLANNED", "K-Core did not plan inference-proven Silero timestamps");
    require(plan.selectedModelId == descriptor.id, "K-Core selected the wrong model");
    require(plan.provider == "cpu" && plan.precision == "fp32", "K-Core runtime selection drifted");
    require(plan.reservedVramGiB == 0.0, "CPU VAD must not reserve VRAM");
    require(plan.maxConcurrent == 4, "Silero concurrency should remain four");
    require(!plan.executionAuthorized && plan.requiresOperatorApproval,
            "K-Core must remain planning-only");
    require(plan.idempotencyKey.find(descriptor.id) != std::string::npos,
            "K-Core idempotency key does not identify Silero");

    auto unproven = request;
    unproven.jobId = "silero-vad-unproven-job";
    unproven.inferenceProofModelIds.clear();
    const auto unprovenPlan = scheduler.plan(unproven, {evidence});
    require(unprovenPlan.status == "BLOCKED", "Integrity-only Silero must be blocked");
    require(contains(unprovenPlan.blockers, "MODEL_INFERENCE_NOT_PROVEN"),
            "Missing inference-proof blocker");

    auto gpuOnly = request;
    gpuOnly.jobId = "silero-vad-gpu-only-job";
    gpuOnly.providerPreference = {"cuda", "tensorrt"};
    const auto gpuPlan = scheduler.plan(gpuOnly, {evidence});
    require(gpuPlan.status == "BLOCKED", "Unproven GPU provider must be blocked");
    require(contains(gpuPlan.blockers, "PROVIDER_NOT_AVAILABLE"),
            "Missing GPU provider blocker");

    auto wrongPurpose = request;
    wrongPurpose.jobId = "silero-vad-wrong-purpose-job";
    wrongPurpose.purpose = "voice_conversion";
    const auto wrongPurposePlan = scheduler.plan(wrongPurpose, {evidence});
    require(wrongPurposePlan.status == "BLOCKED", "Silero must not masquerade as voice conversion");
    require(contains(wrongPurposePlan.blockers, "PURPOSE_NOT_SUPPORTED"),
            "Missing purpose blocker");

    auto vocalIsolation = request;
    vocalIsolation.jobId = "silero-vad-vocal-isolation-job";
    vocalIsolation.requiredCapabilities.push_back("vocalIsolation");
    const auto isolationPlan = scheduler.plan(vocalIsolation, {evidence});
    require(isolationPlan.status == "BLOCKED", "Silero timestamps must not masquerade as vocal isolation");
    require(contains(isolationPlan.blockers, "CAPABILITY_NOT_AVAILABLE"),
            "Missing vocal-isolation capability blocker");

    std::cout << "EchoesSileroKCore PASS"
              << " integrity=verified"
              << " selected=silero-vad-6.2.1"
              << " timestamps=selected"
              << " provider=cpu"
              << " precision=fp32"
              << " vram=0"
              << " concurrency=4"
              << " integrity-only=blocked"
              << " gpu=blocked"
              << " voice-conversion=blocked"
              << " vocal-isolation=blocked"
              << " execution=not-authorized"
              << "\n";
    return 0;
}
