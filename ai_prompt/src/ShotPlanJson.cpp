#include "EchoesEngine/ai_prompt/ShotPlanJson.h"

#include <iomanip>
#include <sstream>

namespace echoes::ai_prompt {
namespace {

std::string escape_json(const std::string& value) {
    std::ostringstream out;
    for (const unsigned char ch : value) {
        switch (ch) {
            case '"': out << "\\\""; break;
            case '\\': out << "\\\\"; break;
            case '\b': out << "\\b"; break;
            case '\f': out << "\\f"; break;
            case '\n': out << "\\n"; break;
            case '\r': out << "\\r"; break;
            case '\t': out << "\\t"; break;
            default:
                if (ch < 0x20) {
                    out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(ch) << std::dec;
                } else {
                    out << static_cast<char>(ch);
                }
        }
    }
    return out.str();
}

void write_string(std::ostringstream& out, const std::string& value) {
    out << '"' << escape_json(value) << '"';
}

} // namespace

std::string ShotPlanJson::serialize(const ShotPlan& plan) {
    std::ostringstream out;
    out << std::fixed << std::setprecision(3);
    out << "{\"schema\":\"echoes.shot-plan.v1\",\"durationSeconds\":" << plan.durationSeconds << ",\"shots\":[";

    for (std::size_t i = 0; i < plan.shots.size(); ++i) {
        const auto& shot = plan.shots[i];
        if (i != 0) out << ',';
        out << '{';
        out << "\"id\":"; write_string(out, shot.id);
        out << ",\"sectionId\":"; write_string(out, shot.sectionId);
        out << ",\"startSeconds\":" << shot.startSeconds;
        out << ",\"durationSeconds\":" << shot.durationSeconds;
        out << ",\"camera\":"; write_string(out, ShotPlanner::camera_move_name(shot.camera));
        out << ",\"transition\":"; write_string(out, shot.transition);
        out << ",\"seed\":" << shot.seed;
        out << ",\"sceneId\":"; write_string(out, shot.scene.id);
        out << ",\"sceneConfidence\":" << shot.scene.confidence;
        out << ",\"prompt\":"; write_string(out, shot.prompt);
        out << '}';
    }

    out << "]}";
    return out.str();
}

} // namespace echoes::ai_prompt
