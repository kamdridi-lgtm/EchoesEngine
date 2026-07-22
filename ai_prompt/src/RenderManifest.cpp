#include "EchoesEngine/ai_prompt/RenderManifest.h"

#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace echoes::ai_prompt {
namespace {

std::string escape_json(const std::string& value) {
    std::ostringstream out;
    for (const unsigned char ch : value) {
        switch (ch) {
            case '\\': out << "\\\\"; break;
            case '"': out << "\\\""; break;
            case '\n': out << "\\n"; break;
            case '\r': out << "\\r"; break;
            case '\t': out << "\\t"; break;
            default:
                if (ch < 0x20) {
                    out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(ch)
                        << std::dec << std::setfill(' ');
                } else {
                    out << static_cast<char>(ch);
                }
        }
    }
    return out.str();
}

std::string normalize_directory(std::string directory) {
    while (!directory.empty() && (directory.back() == '/' || directory.back() == '\\')) {
        directory.pop_back();
    }
    return directory.empty() ? "clips" : directory;
}

} // namespace

RenderManifest RenderManifestBuilder::build(const ShotPlan& plan, const std::string& jobId, const std::string& outputDirectory) {
    if (jobId.empty()) {
        throw std::invalid_argument("render manifest job id must not be empty");
    }
    if (plan.shots.empty()) {
        throw std::invalid_argument("render manifest requires at least one shot");
    }

    RenderManifest manifest;
    manifest.jobId = jobId;
    manifest.durationSeconds = plan.durationSeconds;
    const std::string directory = normalize_directory(outputDirectory);

    manifest.tasks.reserve(plan.shots.size());
    for (std::size_t index = 0; index < plan.shots.size(); ++index) {
        const auto& shot = plan.shots[index];
        RenderTask task;
        task.id = jobId + "-task-" + std::to_string(index + 1);
        task.shotId = shot.id;
        task.startSeconds = shot.startSeconds;
        task.durationSeconds = shot.durationSeconds;
        task.seed = shot.seed;
        task.prompt = shot.prompt;
        task.camera = ShotPlanner::camera_move_name(shot.camera);
        task.transition = shot.transition;
        task.outputFile = directory + "/" + shot.id + ".mp4";
        task.continuity = shot.continuity;
        manifest.tasks.push_back(std::move(task));
    }

    return manifest;
}

std::string RenderManifestBuilder::to_json(const RenderManifest& manifest) {
    std::ostringstream out;
    out << std::fixed << std::setprecision(3);
    out << "{\n";
    out << "  \"schema\": \"" << escape_json(manifest.schema) << "\",\n";
    out << "  \"jobId\": \"" << escape_json(manifest.jobId) << "\",\n";
    out << "  \"durationSeconds\": " << manifest.durationSeconds << ",\n";
    out << "  \"tasks\": [\n";
    for (std::size_t index = 0; index < manifest.tasks.size(); ++index) {
        const auto& task = manifest.tasks[index];
        out << "    {\n";
        out << "      \"id\": \"" << escape_json(task.id) << "\",\n";
        out << "      \"shotId\": \"" << escape_json(task.shotId) << "\",\n";
        out << "      \"startSeconds\": " << task.startSeconds << ",\n";
        out << "      \"durationSeconds\": " << task.durationSeconds << ",\n";
        out << "      \"seed\": " << task.seed << ",\n";
        out << "      \"camera\": \"" << escape_json(task.camera) << "\",\n";
        out << "      \"transition\": \"" << escape_json(task.transition) << "\",\n";
        out << "      \"prompt\": \"" << escape_json(task.prompt) << "\",\n";
        out << "      \"continuity\": {\n";
        out << "        \"subjectId\": \"" << escape_json(task.continuity.subjectId) << "\",\n";
        out << "        \"styleId\": \"" << escape_json(task.continuity.styleId) << "\",\n";
        out << "        \"referenceAsset\": \"" << escape_json(task.continuity.referenceAsset) << "\",\n";
        out << "        \"strength\": " << task.continuity.identityStrength << "\n";
        out << "      },\n";
        out << "      \"outputFile\": \"" << escape_json(task.outputFile) << "\"\n";
        out << "    }" << (index + 1 < manifest.tasks.size() ? "," : "") << "\n";
    }
    out << "  ]\n";
    out << "}\n";
    return out.str();
}

} // namespace echoes::ai_prompt
