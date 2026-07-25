#pragma once

#include "EchoesEngine/neural/OnnxModelManager.h"

#include <algorithm>
#include <cmath>
#include <limits>
#include <set>
#include <string>
#include <vector>

namespace echoes::neural {

struct NeuralScheduleRequest {
    std::string jobId;
    std::string purpose;
    std::vector<std::string> requiredCapabilities;
    std::vector<std::string> providerPreference;
    std::vector<std::string> precisionPreference;
    bool commercialUse = false;
    double availableVramGiB = 0.0;
    double reserveVramGiB = 0.5;
    int minimumQuality = 1;
    int maximumConcurrency = 1;
};

struct NeuralCandidateDecision {
    std::string modelId;
    bool eligible = false;
    int score = std::numeric_limits<int>::min();
    std::string provider;
    std::string precision;
    std::vector<std::string> blockers;
};

struct NeuralScheduleResult {
    std::string schema = "echoes.neural-schedule.v1";
    std::string status = "BLOCKED";
    std::string jobId;
    std::string selectedModelId;
    std::filesystem::path selectedPath;
    std::string selectedSha256;
    std::string provider;
    std::string precision;
    double reservedVramGiB = 0.0;
    int maxConcurrent = 0;
    std::string idempotencyKey;
    std::vector<std::string> blockers;
    std::vector<NeuralCandidateDecision> candidates;
    bool requiresOperatorApproval = true;
    bool executionAuthorized = false;
    bool secretsPersisted = false;
};

class NeuralScheduler final {
public:
    NeuralScheduleResult plan(const NeuralScheduleRequest& request,
                              const std::vector<OnnxModelEvidence>& inventory) const {
        NeuralScheduleResult result;
        result.jobId = request.jobId;
        if (request.jobId.empty()) result.blockers.push_back("JOB_ID_MISSING");
        if (request.purpose.empty()) result.blockers.push_back("PURPOSE_MISSING");
        if (request.availableVramGiB < 0.0 || request.reserveVramGiB < 0.0) result.blockers.push_back("VRAM_INPUT_INVALID");
        if (request.minimumQuality < 1 || request.minimumQuality > 100) result.blockers.push_back("QUALITY_INPUT_INVALID");
        if (request.maximumConcurrency < 1) result.blockers.push_back("CONCURRENCY_INPUT_INVALID");
        if (!result.blockers.empty()) return result;

        const OnnxModelEvidence* winner = nullptr;
        std::size_t winnerIndex = std::numeric_limits<std::size_t>::max();
        int winnerScore = std::numeric_limits<int>::min();
        for (const auto& evidence : inventory) {
            NeuralCandidateDecision decision;
            decision.modelId = evidence.descriptor.id;
            const auto& descriptor = evidence.descriptor;
            if (evidence.status != ModelEvidenceStatus::PASS) decision.blockers.push_back("MODEL_EVIDENCE_NOT_PASS");
            if (descriptor.purpose != request.purpose) decision.blockers.push_back("PURPOSE_NOT_SUPPORTED");
            if (descriptor.qualityScore < request.minimumQuality) decision.blockers.push_back("QUALITY_BELOW_REQUEST");
            if (request.commercialUse && !descriptor.commercialUseAllowed) decision.blockers.push_back("COMMERCIAL_USE_NOT_APPROVED");
            if (descriptor.minimumVramGiB + request.reserveVramGiB > request.availableVramGiB + 1e-9) decision.blockers.push_back("VRAM_BUDGET_EXCEEDED");
            if (!containsAll(descriptor.capabilities, request.requiredCapabilities)) decision.blockers.push_back("CAPABILITIES_MISSING");
            decision.provider = choose(request.providerPreference, descriptor.providers);
            if (decision.provider.empty()) decision.blockers.push_back("PROVIDER_NOT_AVAILABLE");
            decision.precision = choose(request.precisionPreference, descriptor.precisions);
            if (decision.precision.empty()) decision.blockers.push_back("PRECISION_NOT_AVAILABLE");
            decision.eligible = decision.blockers.empty();
            if (decision.eligible) {
                const int providerBonus = preferenceBonus(request.providerPreference, decision.provider);
                const int precisionBonus = preferenceBonus(request.precisionPreference, decision.precision);
                const int vramEfficiency = static_cast<int>(std::max(0.0, 20.0 - descriptor.minimumVramGiB * 2.0));
                decision.score = descriptor.qualityScore * 100 + providerBonus + precisionBonus + vramEfficiency;
            }
            result.candidates.push_back(decision);
            auto& stored = result.candidates.back();
            if (stored.eligible && (winner == nullptr || stored.score > winnerScore ||
                (stored.score == winnerScore && descriptor.id < winner->descriptor.id))) {
                winner = &evidence;
                winnerIndex = result.candidates.size() - 1;
                winnerScore = stored.score;
            }
        }

        if (winner == nullptr || winnerIndex == std::numeric_limits<std::size_t>::max()) {
            std::set<std::string> unique;
            for (const auto& decision : result.candidates) unique.insert(decision.blockers.begin(), decision.blockers.end());
            result.blockers.assign(unique.begin(), unique.end());
            if (result.blockers.empty()) result.blockers.push_back("NO_MODELS_REGISTERED");
            return result;
        }

        const auto& descriptor = winner->descriptor;
        const double usable = std::max(0.0, request.availableVramGiB - request.reserveVramGiB);
        int byVram = descriptor.minimumVramGiB > 0.0
            ? static_cast<int>(std::floor(usable / descriptor.minimumVramGiB))
            : request.maximumConcurrency;
        byVram = std::max(1, byVram);
        result.status = "PLANNED";
        result.selectedModelId = descriptor.id;
        result.selectedPath = winner->resolvedPath;
        result.selectedSha256 = winner->actualSha256;
        result.provider = result.candidates[winnerIndex].provider;
        result.precision = result.candidates[winnerIndex].precision;
        result.reservedVramGiB = descriptor.minimumVramGiB;
        result.maxConcurrent = std::min({request.maximumConcurrency, descriptor.maxConcurrency, byVram});
        result.idempotencyKey = request.jobId + ":" + descriptor.id + ":" + winner->actualSha256.substr(0, 16);
        return result;
    }

private:
    static bool containsAll(const std::vector<std::string>& available,
                            const std::vector<std::string>& required) {
        for (const auto& value : required) {
            if (std::find(available.begin(), available.end(), value) == available.end()) return false;
        }
        return true;
    }

    static std::string choose(const std::vector<std::string>& preference,
                              const std::vector<std::string>& available) {
        if (!preference.empty()) {
            for (const auto& requested : preference) {
                if (std::find(available.begin(), available.end(), requested) != available.end()) return requested;
            }
            return {};
        }
        return available.empty() ? std::string{} : available.front();
    }

    static int preferenceBonus(const std::vector<std::string>& preference,
                               const std::string& selected) {
        if (preference.empty()) return 0;
        const auto found = std::find(preference.begin(), preference.end(), selected);
        if (found == preference.end()) return 0;
        return static_cast<int>((preference.size() - static_cast<std::size_t>(std::distance(preference.begin(), found))) * 10U);
    }
};

} // namespace echoes::neural
