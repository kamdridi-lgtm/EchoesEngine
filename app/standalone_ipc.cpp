/**
 * standalone_ipc.cpp  —  EchoesEngine IPC Server Mode
 * =====================================================
 *
 * Mode 2: launched by the bridge as a subprocess.
 * Reads JSON commands from stdin, writes JSON stats to stdout.
 *
 * stdin  (one line per command):
 *   {"cmd":"set_param","param":"inputGainDb","value":6.0}
 *   {"cmd":"set_param","param":"aiEnabled","value":true}
 *   {"cmd":"set_param","param":"pitchShift","value":2.0}
 *   {"cmd":"set_param","param":"timbreDepth","value":0.5}
 *   {"cmd":"set_param","param":"breathPreservation","value":0.8}
 *   {"cmd":"quit"}
 *
 * stdout (one line per 50ms tick):
 *   {"type":"stats","inputDb":-12.3,"outputDb":-14.1,"compGR":-3.2,
 *    "gateOpen":true,"clipping":false,"aiBackend":"ONNX CPU",
 *    "pitchHz":220.5,"pitchConfidence":0.87,"pitchVoiced":true,
 *    "lastBlockMs":0.043,"aiInferMs":0.001,"pitchInferMs":0.001,
 *    "trtBuilding":-1}
 *
 * Usage (launched by bridge):
 *   ./EchoesEngine --ipc
 *
 * Build:
 *   g++ -std=c++20 -O2 -I engine/include \
 *       engine/src/Engine.cpp app/standalone_ipc.cpp \
 *       -o EchoesEngine
 */

#include "EchoesEngine/Engine.h"
#include "EchoesEngine/audio/AudioBuffer.h"

#include <iostream>
#include <string>
#include <thread>
#include <atomic>
#include <mutex>
#include <queue>
#include <chrono>
#include <cmath>
#include <sstream>
#include <iomanip>

// ─── Tiny JSON parser (no dependencies) ──────────────────────────────────────
// Just enough to parse {"cmd":..., "param":..., "value":...}
struct JsonMsg {
    std::string cmd;
    std::string param;
    std::string strVal;
    double      numVal = 0.0;
    bool        boolVal= false;
    bool        isNum  = false;
    bool        isBool = false;

    static std::string extractStr(const std::string& s, const std::string& key) {
        auto k = s.find("\"" + key + "\"");
        if (k == std::string::npos) return "";
        auto colon = s.find(':', k);
        if (colon == std::string::npos) return "";
        auto start = s.find('"', colon + 1);
        if (start == std::string::npos) return "";
        auto end = s.find('"', start + 1);
        if (end == std::string::npos) return "";
        return s.substr(start + 1, end - start - 1);
    }

    static bool parse(const std::string& line, JsonMsg& out) {
        if (line.empty() || line[0] != '{') return false;
        out.cmd   = extractStr(line, "cmd");
        out.param = extractStr(line, "param");
        if (out.cmd.empty()) return false;

        // Find "value": ... (number, bool, or string)
        auto vk = line.find("\"value\"");
        if (vk != std::string::npos) {
            auto colon = line.find(':', vk);
            if (colon != std::string::npos) {
                size_t vs = colon + 1;
                while (vs < line.size() && std::isspace(line[vs])) vs++;
                if (vs < line.size()) {
                    if (line[vs] == '"') {
                        out.strVal = extractStr(line, "value");
                    } else if (line.substr(vs,4) == "true") {
                        out.boolVal = true; out.isBool = true;
                    } else if (line.substr(vs,5) == "false") {
                        out.boolVal = false; out.isBool = true;
                    } else {
                        try {
                            out.numVal = std::stod(line.substr(vs));
                            out.isNum  = true;
                        } catch (...) {}
                    }
                }
            }
        }
        return true;
    }
};

// ─── JSON stats builder ───────────────────────────────────────────────────────
static std::string statsToJson(const echoes::ExtendedStats& s,
                                 float inputDbL, float inputDbR,
                                 float outputDbL, float outputDbR) {
    auto f2s = [](double v, int prec=2) -> std::string {
        std::ostringstream o; o << std::fixed << std::setprecision(prec) << v;
        return o.str();
    };
    auto b2s = [](bool v) -> std::string { return v ? "true" : "false"; };
    auto q   = [](const std::string& s) -> std::string { return "\""+s+"\""; };

    return std::string("{")
        + "\"type\":\"stats\","
        + "\"inputDb\":"    + f2s(s.inputPeakDb)  + ","
        + "\"inputDbL\":"   + f2s(inputDbL)        + ","
        + "\"inputDbR\":"   + f2s(inputDbR)        + ","
        + "\"outputDb\":"   + f2s(s.outputPeakDb)  + ","
        + "\"outputDbL\":"  + f2s(outputDbL)       + ","
        + "\"outputDbR\":"  + f2s(outputDbR)       + ","
        + "\"compGR\":"     + f2s(s.compressionDb) + ","
        + "\"gateOpen\":"   + b2s(s.gateOpen)      + ","
        + "\"clipping\":"   + b2s(s.outputPeakDb > -0.5f) + ","
        + "\"aiBackend\":"  + q(s.aiBackend)       + ","
        + "\"aiActive\":"   + b2s(s.aiActive)      + ","
        + "\"pitchHz\":"    + f2s(s.pitchHz, 2)    + ","
        + "\"pitchConf\":"  + f2s(s.pitchConfidence, 3) + ","
        + "\"pitchVoiced\":" + b2s(s.pitchVoiced)  + ","
        + "\"pitchBackend\":" + q(s.pitchBackend)  + ","
        + "\"lastBlockMs\":" + f2s(s.lastBlockMs, 4) + ","
        + "\"aiInferMs\":"   + f2s(s.aiInferMs, 4) + ","
        + "\"pitchInferMs\":" + f2s(s.pitchInferMs, 4) + ","
        + "\"trtBuilding\":"      + f2s(s.trtBuilding, 1) + ","
        + "\"perfMs\":"           + f2s(s.perfMs, 4) + ","
        + "\"emotionalState\":"   + std::to_string(s.emotionalState) + ","
        + "\"emotionalInt\":"     + f2s(s.emotionalIntensity, 3) + ","
        + "\"fatigue\":"          + f2s(s.emotionalFatigue, 3) + ","
        + "\"narrative\":"        + f2s(s.narrativeInfluence, 3) + ","
        + "\"naturalness\":"      + f2s(s.naturalnessScore, 3) + ","
        + "\"stereoHealth\":"     + f2s(s.stereoHealth, 3) + ","
        + "\"monoSafe\":"         + b2s(s.monoSafe) + ","
        + "\"stereoCorr\":"       + f2s(s.stereoCorrelation, 3)
        + "}";
}

// ─── IPC main ─────────────────────────────────────────────────────────────────
int run_ipc_mode() {
    // Redirect stderr so bridge only sees JSON on stdout
    // (engine logs still go to stderr which bridge can capture separately)
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);

    echoes::EchoesEngine::Config cfg;
    cfg.sampleRate  = 44100.0f;
    cfg.blockSize   = 512;
    cfg.numChannels = 2;
    cfg.gateEnabled = true;
    cfg.compEnabled = true;
    cfg.eqEnabled   = true;
    cfg.aiEnabled   = false;

    echoes::EchoesEngine engine(cfg);
    if (!engine.init()) {
        std::cerr << "[IPC] Engine init failed\n";
        return 1;
    }

    // Signal ready
    std::cout << "{\"type\":\"ready\",\"version\":\""
              << echoes::EchoesEngine::version() << "\"}" << std::endl;

    // ── Audio generation thread (simulates real-time input) ──────────────────
    // In production: replace with PortAudio / ASIO callback
    std::atomic<bool> running{true};
    std::atomic<float> inputDbL{-120.f}, inputDbR{-120.f};
    std::atomic<float> outputDbL{-120.f}, outputDbR{-120.f};
    float phase = 0.f;
    const float sr = cfg.sampleRate;
    const int   bs = cfg.blockSize;

    // Command queue (written by stdin thread, read by audio thread)
    std::mutex cmdMu;
    std::queue<JsonMsg> cmdQueue;

    // Audio thread: processes blocks at real-time rate and emits stats
    std::thread audioThread([&]() {
        std::vector<float> buf(bs * 2);
        const float dt = static_cast<float>(bs) / sr;

        while (running.load()) {
            // Apply queued commands
            {
                std::lock_guard lk(cmdMu);
                while (!cmdQueue.empty()) {
                    auto& m = cmdQueue.front();
                    // Route every param
                    if      (m.param == "inputGainDb")       engine.setInputGainDb(m.numVal);
                    else if (m.param == "outputGainDb")      engine.setOutputGainDb(m.numVal);
                    else if (m.param == "gateThreshDb")      engine.setGateThreshold(m.numVal);
                    else if (m.param == "compThreshDb")      engine.setCompThreshold(m.numVal);
                    else if (m.param == "compRatio")         engine.setCompRatio(m.numVal);
                    else if (m.param == "gateEnabled")       engine.setGateEnabled(m.boolVal);
                    else if (m.param == "compEnabled")       engine.setCompEnabled(m.boolVal);
                    else if (m.param == "eqEnabled")         engine.setEQEnabled(m.boolVal);
                    else if (m.param == "aiEnabled")         engine.setAIEnabled(m.boolVal);
                    else if (m.param == "pitchShift")        engine.setAIPitchShift(m.numVal);
                    else if (m.param == "timbreDepth")       engine.setAITimbreDepth(m.numVal);
                    else if (m.param == "breathPreservation") engine.setAIBreathPreservation(m.numVal);
                    else if (m.param == "voiceModel" && !m.strVal.empty())
                        engine.loadVoiceModel(m.strVal);
                    // Emotional params
                    else if (m.param == "emotionalState")
                        engine.setEmotionalState(static_cast<int>(m.numVal),
                            engine.getStats().compressionDb != 0 ? 0.7f : 0.5f);
                    else if (m.param == "emotionalIntensity")
                        engine.setEmotionalIntensity(static_cast<float>(m.numVal));
                    else if (m.param == "emotionalEnabled")
                        engine.setEmotionalEnabled(m.boolVal);
                    else if (m.param == "bpm")
                        engine.setBPM(static_cast<float>(m.numVal));
                    cmdQueue.pop();
                }
            }

            // Generate synthetic vocal signal (replace with PortAudio input)
            const float f0 = 220.f;
            for (int f = 0; f < bs; ++f) {
                float t = phase + static_cast<float>(f) / sr;
                float env = 0.7f + 0.2f * std::sin(t * 1.1f);
                float s =  env * (0.6f * std::sin(2.f * 3.14159f * f0 * t)
                               + 0.25f * std::sin(2.f * 3.14159f * f0 * 2 * t)
                               + 0.10f * std::sin(2.f * 3.14159f * f0 * 3 * t));
                float spread = 0.005f * std::sin(t * 7.3f);
                buf[f*2+0] = s - spread;
                buf[f*2+1] = s + spread;
            }
            phase += dt;

            // Store pre-process L/R peak
            float ipL = -120.f, ipR = -120.f;
            for (int f = 0; f < bs; ++f) {
                ipL = std::max(ipL, std::abs(buf[f*2+0]));
                ipR = std::max(ipR, std::abs(buf[f*2+1]));
            }
            inputDbL.store(ipL > 1e-9f ? 20.f*std::log10(ipL) : -120.f);
            inputDbR.store(ipR > 1e-9f ? 20.f*std::log10(ipR) : -120.f);

            engine.process(buf.data(), bs * 2, 2);

            // Post-process L/R peak
            float opL = -120.f, opR = -120.f;
            for (int f = 0; f < bs; ++f) {
                opL = std::max(opL, std::abs(buf[f*2+0]));
                opR = std::max(opR, std::abs(buf[f*2+1]));
            }
            outputDbL.store(opL > 1e-9f ? 20.f*std::log10(opL) : -120.f);
            outputDbR.store(opR > 1e-9f ? 20.f*std::log10(opR) : -120.f);

            // Emit stats every block (bridge throttles to 20Hz)
            auto es = engine.getExtendedStats();
            std::string json = statsToJson(es,
                inputDbL.load(), inputDbR.load(),
                outputDbL.load(), outputDbR.load());
            std::cout << json << "\n";
            std::cout.flush();

            // Real-time pacing: sleep for one block duration
            std::this_thread::sleep_for(
                std::chrono::microseconds(static_cast<int>(dt * 1e6)));
        }
    });

    // ── Stdin reader (main thread) ───────────────────────────────────────────
    std::string line;
    while (running.load() && std::getline(std::cin, line)) {
        if (line.empty()) continue;
        JsonMsg msg;
        if (!JsonMsg::parse(line, msg)) continue;

        if (msg.cmd == "quit") {
            running.store(false);
            break;
        } else if (msg.cmd == "set_param") {
            std::lock_guard lk(cmdMu);
            cmdQueue.push(msg);
        }
    }

    running.store(false);
    if (audioThread.joinable()) audioThread.join();
    engine.shutdown();

    std::cout << "{\"type\":\"shutdown\"}" << std::endl;
    return 0;
}

// ─── Entry point (shared with standalone_processor.cpp via compile unit switch)
// Compile with -DIPC_MODE to get this main instead of standalone
#ifdef IPC_MODE
int main(int argc, char* argv[]) {
    (void)argc; (void)argv;
    return run_ipc_mode();
}
#endif
