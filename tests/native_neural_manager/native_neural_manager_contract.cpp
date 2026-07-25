#include "EchoesEngine/neural/NeuralScheduler.h"

#include <filesystem>
#include <fstream>
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

static void writeBytes(const fs::path& path, const std::string& data) {
    fs::create_directories(path.parent_path());
    std::ofstream output(path, std::ios::binary);
    output.write(data.data(), static_cast<std::streamsize>(data.size()));
    if (!output) throw std::runtime_error("unable to write fixture");
}

static OnnxModelDescriptor descriptor(const std::string& id,
                                      const fs::path& relative,
                                      const fs::path& absolute,
                                      double vram,
                                      int quality,
                                      bool commercial,
                                      std::vector<std::string> providers,
                                      std::vector<std::string> precisions) {
    OnnxModelDescriptor value;
    value.id = id;
    value.purpose = "voice_conversion";
    value.relativePath = relative;
    value.expectedSha256 = OnnxModelManager::sha256File(absolute);
    value.expectedSizeBytes = fs::file_size(absolute);
    value.minimumVramGiB = vram;
    value.qualityScore = quality;
    value.maxConcurrency = 2;
    value.commercialUseAllowed = commercial;
    value.providers = std::move(providers);
    value.precisions = std::move(precisions);
    value.capabilities = {"voiceConversion", "seededInference"};
    return value;
}

int main() {
    const fs::path root = fs::temp_directory_path() / "echoes-native-neural-manager-contract";
    std::error_code error;
    fs::remove_all(root, error);
    fs::create_directories(root / "models");
    const fs::path smallPath = root / "models" / "voice-small.onnx";
    const fs::path largePath = root / "models" / "voice-large.onnx";
    writeBytes(smallPath, "synthetic-onnx-fixture-small-v1\n");
    writeBytes(largePath, "synthetic-onnx-fixture-large-quality-v1\n");

    OnnxModelManager manager(root);
    auto small = descriptor("voice-small", "models/voice-small.onnx", smallPath, 3.0, 82, true,
                            {"cuda", "cpu"}, {"fp16", "fp32"});
    auto large = descriptor("voice-large", "models/voice-large.onnx", largePath, 5.5, 94, false,
                            {"cuda"}, {"fp16"});
    const auto smallEvidence = manager.inspect(small);
    const auto largeEvidence = manager.inspect(large);
    require(smallEvidence.status == ModelEvidenceStatus::PASS, "small model integrity should pass");
    require(largeEvidence.status == ModelEvidenceStatus::PASS, "large model integrity should pass");
    require(smallEvidence.networkRequested == false && smallEvidence.executableLoaded == false,
            "manager must remain offline and must not load executable model code");

    auto drift = small;
    drift.id = "voice-drift";
    drift.expectedSha256 = std::string(64, '0');
    const auto driftEvidence = manager.inspect(drift);
    require(driftEvidence.status == ModelEvidenceStatus::BLOCKED, "hash drift must block");
    require(std::find(driftEvidence.blockers.begin(), driftEvidence.blockers.end(), "MODEL_SHA256_MISMATCH") != driftEvidence.blockers.end(),
            "hash mismatch blocker missing");

    auto missing = small;
    missing.id = "voice-missing";
    missing.relativePath = "models/missing.onnx";
    const auto missingEvidence = manager.inspect(missing);
    require(missingEvidence.status == ModelEvidenceStatus::BLOCKED, "missing model must block");

    auto escape = small;
    escape.id = "voice-escape";
    escape.relativePath = "../escape.onnx";
    const auto escapeEvidence = manager.inspect(escape);
    require(escapeEvidence.status == ModelEvidenceStatus::BLOCKED, "path escape must block");

    auto dormant = small;
    dormant.id = "voice-disabled";
    dormant.enabled = false;
    const auto dormantEvidence = manager.inspect(dormant);
    require(dormantEvidence.status == ModelEvidenceStatus::DORMANT, "disabled model must be dormant");

    const std::vector inventory = {smallEvidence, largeEvidence, driftEvidence, missingEvidence, dormantEvidence};
    NeuralScheduler scheduler;

    NeuralScheduleRequest quality;
    quality.jobId = "voice-job-quality";
    quality.purpose = "voice_conversion";
    quality.requiredCapabilities = {"voiceConversion"};
    quality.providerPreference = {"cuda", "cpu"};
    quality.precisionPreference = {"fp16", "fp32"};
    quality.availableVramGiB = 6.0;
    quality.reserveVramGiB = 0.25;
    quality.minimumQuality = 90;
    quality.maximumConcurrency = 2;
    const auto qualityPlan = scheduler.plan(quality, inventory);
    require(qualityPlan.status == "PLANNED" && qualityPlan.selectedModelId == "voice-large",
            "quality request should choose large model");
    require(qualityPlan.executionAuthorized == false && qualityPlan.requiresOperatorApproval,
            "scheduler must remain planning-only");

    NeuralScheduleRequest commercial = quality;
    commercial.jobId = "voice-job-commercial";
    commercial.commercialUse = true;
    commercial.minimumQuality = 80;
    const auto commercialPlan = scheduler.plan(commercial, inventory);
    require(commercialPlan.status == "PLANNED" && commercialPlan.selectedModelId == "voice-small",
            "commercial request should avoid non-commercial model");
    require(commercialPlan.maxConcurrent == 1, "VRAM budget should cap concurrency to one");

    NeuralScheduleRequest lowVram = quality;
    lowVram.jobId = "voice-job-low-vram";
    lowVram.availableVramGiB = 4.0;
    const auto lowVramPlan = scheduler.plan(lowVram, inventory);
    require(lowVramPlan.status == "BLOCKED", "insufficient VRAM plus quality must block");
    require(std::find(lowVramPlan.blockers.begin(), lowVramPlan.blockers.end(), "VRAM_BUDGET_EXCEEDED") != lowVramPlan.blockers.end(),
            "VRAM blocker missing");

    NeuralScheduleRequest provider = commercial;
    provider.jobId = "voice-job-provider";
    provider.providerPreference = {"tensorrt"};
    const auto providerPlan = scheduler.plan(provider, inventory);
    require(providerPlan.status == "BLOCKED", "unavailable provider must block");

    NeuralScheduleRequest capability = commercial;
    capability.jobId = "voice-job-capability";
    capability.requiredCapabilities = {"voiceConversion", "speakerEmbedding"};
    const auto capabilityPlan = scheduler.plan(capability, inventory);
    require(capabilityPlan.status == "BLOCKED", "missing capability must block");

    require(OnnxModelManager::sha256File(smallPath) == small.expectedSha256, "SHA-256 must be deterministic");
    require(small.expectedSha256 == "e23c19511783c323478ccae4e222ce1d086d6f7a6ae58164053bc007a9f5130e",
            "SHA-256 implementation does not match the canonical fixture digest");

    std::cout << "EchoesNativeNeuralManager PASS"
              << " integrity=verified"
              << " drift=blocked"
              << " path-escape=blocked"
              << " quality=" << qualityPlan.selectedModelId
              << " commercial=" << commercialPlan.selectedModelId
              << " low-vram=blocked"
              << " provider=blocked"
              << " capability=blocked"
              << " execution=not-authorized"
              << "\n";
    fs::remove_all(root, error);
    return 0;
}
