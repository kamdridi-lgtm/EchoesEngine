#include "EchoesEngine/ai_prompt/PromptDirector.h"
#include "EchoesEngine/api/ApiServer.h"
#include "EchoesEngine/api/JobRegistry.h"
#include "EchoesEngine/audio/AudioAnalyzer.h"
#include "EchoesEngine/export/VideoExporter.h"
#include "EchoesEngine/platform/AutonomousPlatform.h"
#include "EchoesEngine/scene/SceneFactory.h"
#include "opencl/opencl_context.h"
#include "AIVideoPipeline.h"
#include "CameraDirector.h"
#include "RenderPipeline.h"
#include "SceneRenderer.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <optional>
#include <span>
#include <sstream>
#include <string>
#include <string_view>
#include <thread>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace {

struct WavData {
    uint16_t channels = 0;
    uint32_t sampleRate = 0;
    std::vector<float> monoSamples;
};

struct StylePreset {
    std::string sceneId;
    std::string styleName;
    std::string scenePrompt;
    float orbitRadius = 9.0f;
    float orbitSpeed = 0.55f;
    float orbitHeight = 2.0f;
};

struct RenderProfile {
    uint32_t width = 1280;
    uint32_t height = 720;
    uint32_t fps = 30;
    uint32_t keyframeInterval = 60;
};

struct TimelineSample {
    double timeSeconds = 0.0;
    echoes::audio::RealtimeFeatures features;
};

struct AudioTimeline {
    double durationSeconds = 0.0;
    std::size_t totalFrames = 1;
    std::vector<TimelineSample> samples;
    std::vector<echoes::audio::RealtimeFeatures> frameFeatures;
    echoes::audio::RealtimeFeatures summary;

    const echoes::audio::RealtimeFeatures& featureAt(std::size_t frameIndex) const {
        if (frameFeatures.empty()) {
            return summary;
        }
        return frameFeatures[std::min(frameIndex, frameFeatures.size() - 1)];
    }
};

template <typename T>
T readLittleEndian(std::istream& input) {
    T value{};
    input.read(reinterpret_cast<char*>(&value), sizeof(T));
    return value;
}

std::optional<WavData> loadWavFile(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        return std::nullopt;
    }

    std::array<char, 4> riff{};
    input.read(riff.data(), riff.size());
    input.ignore(4);
    std::array<char, 4> wave{};
    input.read(wave.data(), wave.size());
    if (std::string_view(riff.data(), riff.size()) != "RIFF" ||
        std::string_view(wave.data(), wave.size()) != "WAVE") {
        return std::nullopt;
    }

    uint16_t formatTag = 0;
    uint16_t channels = 0;
    uint32_t sampleRate = 0;
    uint16_t bitsPerSample = 0;
    std::vector<char> pcmData;

    while (input && !input.eof()) {
        std::array<char, 4> chunkId{};
        input.read(chunkId.data(), chunkId.size());
        if (input.gcount() != static_cast<std::streamsize>(chunkId.size())) {
            break;
        }

        const uint32_t chunkSize = readLittleEndian<uint32_t>(input);
        const std::string_view id(chunkId.data(), chunkId.size());

        if (id == "fmt ") {
            formatTag = readLittleEndian<uint16_t>(input);
            channels = readLittleEndian<uint16_t>(input);
            sampleRate = readLittleEndian<uint32_t>(input);
            input.ignore(6);
            bitsPerSample = readLittleEndian<uint16_t>(input);
            if (chunkSize > 16) {
                input.ignore(chunkSize - 16);
            }
        } else if (id == "data") {
            pcmData.resize(chunkSize);
            input.read(pcmData.data(), static_cast<std::streamsize>(chunkSize));
        } else {
            input.ignore(chunkSize);
        }

        if (chunkSize % 2 != 0) {
            input.ignore(1);
        }
    }

    if (pcmData.empty() || channels == 0 || sampleRate == 0) {
        return std::nullopt;
    }

    WavData wav;
    wav.channels = channels;
    wav.sampleRate = sampleRate;

    auto appendSample = [&](float sample) {
        wav.monoSamples.push_back(std::clamp(sample, -1.0f, 1.0f));
    };

    if (formatTag == 1 && bitsPerSample == 16) {
        const std::size_t frameCount = pcmData.size() / (sizeof(int16_t) * channels);
        const auto* samples = reinterpret_cast<const int16_t*>(pcmData.data());
        wav.monoSamples.reserve(frameCount);
        for (std::size_t frame = 0; frame < frameCount; ++frame) {
            float mono = 0.0f;
            for (uint16_t ch = 0; ch < channels; ++ch) {
                mono += static_cast<float>(samples[frame * channels + ch]) / 32768.0f;
            }
            appendSample(mono / static_cast<float>(channels));
        }
        return wav;
    }

    if (formatTag == 1 && bitsPerSample == 24) {
        const std::size_t bytesPerFrame = 3u * channels;
        const std::size_t frameCount = pcmData.size() / bytesPerFrame;
        wav.monoSamples.reserve(frameCount);
        for (std::size_t frame = 0; frame < frameCount; ++frame) {
            float mono = 0.0f;
            for (uint16_t ch = 0; ch < channels; ++ch) {
                const std::size_t offset = frame * bytesPerFrame + ch * 3u;
                int32_t sample =
                    static_cast<unsigned char>(pcmData[offset + 0]) |
                    (static_cast<unsigned char>(pcmData[offset + 1]) << 8) |
                    (static_cast<unsigned char>(pcmData[offset + 2]) << 16);
                if ((sample & 0x00800000) != 0) {
                    sample |= ~0x00FFFFFF;
                }
                mono += static_cast<float>(sample) / 8388608.0f;
            }
            appendSample(mono / static_cast<float>(channels));
        }
        return wav;
    }

    if (formatTag == 3 && bitsPerSample == 32) {
        const std::size_t frameCount = pcmData.size() / (sizeof(float) * channels);
        const auto* samples = reinterpret_cast<const float*>(pcmData.data());
        wav.monoSamples.reserve(frameCount);
        for (std::size_t frame = 0; frame < frameCount; ++frame) {
            float mono = 0.0f;
            for (uint16_t ch = 0; ch < channels; ++ch) {
                mono += samples[frame * channels + ch];
            }
            appendSample(mono / static_cast<float>(channels));
        }
        return wav;
    }

    return std::nullopt;
}

std::optional<std::filesystem::path> findFfmpegExecutable() {
    const auto root = std::filesystem::current_path().parent_path() / "tools" / "ffmpeg";
    if (!std::filesystem::exists(root)) {
        return std::nullopt;
    }

    for (const auto& entry : std::filesystem::recursive_directory_iterator(root)) {
        if (entry.is_regular_file() && entry.path().filename() == "ffmpeg.exe") {
            return entry.path();
        }
    }

    return std::nullopt;
}

std::string formatUtcNow() {
    const auto now = std::chrono::system_clock::now();
    const auto timeT = std::chrono::system_clock::to_time_t(now);
    std::tm utcTime{};
#ifdef _WIN32
    gmtime_s(&utcTime, &timeT);
#else
    gmtime_r(&timeT, &utcTime);
#endif
    std::ostringstream oss;
    oss << std::put_time(&utcTime, "%Y-%m-%dT%H:%M:%SZ");
    return oss.str();
}

std::optional<uint32_t> readEnvUint32(const char* name) {
    const char* value = std::getenv(name);
    if (value == nullptr || *value == '\0') {
        return std::nullopt;
    }

    try {
        return static_cast<uint32_t>(std::stoul(value));
    } catch (...) {
        return std::nullopt;
    }
}

class JobLogger {
public:
    explicit JobLogger(std::filesystem::path logPath)
        : logPath_(std::move(logPath)) {
        std::filesystem::create_directories(logPath_.parent_path());
        stream_.open(logPath_, std::ios::app);
    }

    const std::filesystem::path& path() const noexcept {
        return logPath_;
    }

    void info(const std::string& message) {
        write("INFO", message, false);
    }

    void error(const std::string& message) {
        write("ERROR", message, true);
    }

private:
    void write(const char* level, const std::string& message, bool stderrStream) {
        const auto line = "[" + formatUtcNow() + "][" + level + "] " + message;
        std::lock_guard lock(mutex_);
        if (stream_) {
            stream_ << line << '\n';
            stream_.flush();
        }

        if (stderrStream) {
            std::cerr << line << '\n';
        } else {
            std::cout << line << '\n';
        }
    }

    std::filesystem::path logPath_;
    std::ofstream stream_;
    std::mutex mutex_;
};

bool runProcess(const std::wstring& commandLine,
                DWORD& exitCode,
                const std::filesystem::path* logPath = nullptr) {
#ifdef _WIN32
    STARTUPINFOW startupInfo{};
    startupInfo.cb = sizeof(startupInfo);
    PROCESS_INFORMATION processInfo{};
    std::wstring mutableCommand = commandLine;
    HANDLE logHandle = nullptr;

    if (logPath != nullptr) {
        SECURITY_ATTRIBUTES securityAttributes{};
        securityAttributes.nLength = sizeof(securityAttributes);
        securityAttributes.bInheritHandle = TRUE;

        logHandle = CreateFileW(
            logPath->wstring().c_str(),
            FILE_APPEND_DATA,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            &securityAttributes,
            OPEN_ALWAYS,
            FILE_ATTRIBUTE_NORMAL,
            nullptr);
        if (logHandle != INVALID_HANDLE_VALUE) {
            startupInfo.dwFlags |= STARTF_USESTDHANDLES;
            startupInfo.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
            startupInfo.hStdOutput = logHandle;
            startupInfo.hStdError = logHandle;
        } else {
            logHandle = nullptr;
        }
    }

    if (!CreateProcessW(
            nullptr,
            mutableCommand.data(),
            nullptr,
            nullptr,
            logHandle != nullptr ? TRUE : FALSE,
            CREATE_NO_WINDOW,
            nullptr,
            nullptr,
            &startupInfo,
            &processInfo)) {
        exitCode = static_cast<DWORD>(-1);
        if (logHandle != nullptr) {
            CloseHandle(logHandle);
        }
        return false;
    }

    WaitForSingleObject(processInfo.hProcess, INFINITE);
    GetExitCodeProcess(processInfo.hProcess, &exitCode);
    CloseHandle(processInfo.hThread);
    CloseHandle(processInfo.hProcess);
    if (logHandle != nullptr) {
        CloseHandle(logHandle);
    }
    return true;
#else
    (void)commandLine;
    exitCode = static_cast<DWORD>(-1);
    return false;
#endif
}

echoes::camera::AudioFeatures toCameraFeatures(const echoes::audio::RealtimeFeatures& features) {
    return {
        features.bass,
        features.mid,
        features.treble,
        std::clamp(features.energy, 0.0f, 1.0f),
        features.beat,
        features.tempo > 0.0f ? features.tempo : 96.0f,
    };
}

echoes::ai_prompt::AudioFeatures toPromptFeatures(const echoes::audio::RealtimeFeatures& features) {
    return {
        features.bass,
        features.mid,
        features.treble,
        std::clamp(features.energy, 0.0f, 1.0f),
        features.tempo > 0.0f ? features.tempo : 96.0f,
        features.beat,
    };
}

echoes::camera::Vec3 toCameraVec3(const echoes::scene::Vec3& value) {
    return {value.x, value.y, value.z};
}

std::string toLower(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return value;
}

std::optional<std::filesystem::path> prepareAudioForAnalysis(const std::filesystem::path& sourcePath,
                                                             const std::filesystem::path* logPath = nullptr) {
    if (!std::filesystem::exists(sourcePath)) {
        return std::nullopt;
    }

    const auto extension = toLower(sourcePath.extension().string());
    if (extension == ".wav") {
        return sourcePath;
    }

    const auto ffmpegPath = findFfmpegExecutable();
    if (!ffmpegPath) {
        return std::nullopt;
    }

    const auto wavPath = sourcePath.parent_path() / "audio.wav";
    std::filesystem::remove(wavPath);

    std::wostringstream ffmpegCommand;
    ffmpegCommand << L"\"" << ffmpegPath->wstring() << L"\""
                  << L" -y"
                  << L" -i \"" << sourcePath.wstring() << L"\""
                  << L" -vn"
                  << L" -ac 1"
                  << L" -ar 48000"
                  << L" -sample_fmt s16"
                  << L" \"" << wavPath.wstring() << L"\"";

    DWORD ffmpegExitCode = 1;
    const bool launched = runProcess(ffmpegCommand.str(), ffmpegExitCode, logPath);
    if (!launched || ffmpegExitCode != 0 || !std::filesystem::exists(wavPath)) {
        return std::nullopt;
    }

    return wavPath;
}

RenderProfile loadRenderProfile() {
    RenderProfile profile;
    if (const auto width = readEnvUint32("ECHOES_RENDER_WIDTH")) {
        profile.width = std::max<uint32_t>(640, *width);
    }
    if (const auto height = readEnvUint32("ECHOES_RENDER_HEIGHT")) {
        profile.height = std::max<uint32_t>(360, *height);
    }
    if (const auto fps = readEnvUint32("ECHOES_RENDER_FPS")) {
        profile.fps = std::clamp<uint32_t>(*fps, 24, 60);
    }
    profile.keyframeInterval = std::max<uint32_t>(profile.fps, 30u);
    return profile;
}

AudioTimeline buildAudioTimeline(const WavData& wav, uint32_t fps) {
    AudioTimeline timeline;
    timeline.durationSeconds = wav.sampleRate == 0
        ? 0.0
        : static_cast<double>(wav.monoSamples.size()) / static_cast<double>(wav.sampleRate);
    timeline.totalFrames = std::max<std::size_t>(
        1,
        static_cast<std::size_t>(std::ceil(timeline.durationSeconds * static_cast<double>(std::max<uint32_t>(fps, 1)))));

    echoes::audio::AudioAnalyzer analyzer({2048, 512, 43, 1.35f, 0.2f, 0.3f});
    constexpr std::size_t blockSize = 2048;
    constexpr std::size_t hopSize = 512;

    for (std::size_t offset = 0; offset < wav.monoSamples.size(); offset += hopSize) {
        const auto available = std::min(blockSize, wav.monoSamples.size() - offset);
        analyzer.processBlock(
            std::span<const float>(wav.monoSamples.data() + offset, available),
            static_cast<double>(wav.sampleRate));
        timeline.samples.push_back({
            static_cast<double>(offset) / static_cast<double>(wav.sampleRate),
            analyzer.features()
        });
    }

    if (timeline.samples.empty()) {
        timeline.samples.push_back({0.0, {}});
    }

    timeline.frameFeatures.reserve(timeline.totalFrames);
    std::size_t sampleIndex = 0;
    for (std::size_t frameIndex = 0; frameIndex < timeline.totalFrames; ++frameIndex) {
        const double timeSeconds = static_cast<double>(frameIndex) / static_cast<double>(std::max<uint32_t>(fps, 1));
        while (sampleIndex + 1 < timeline.samples.size() &&
               timeline.samples[sampleIndex + 1].timeSeconds <= timeSeconds) {
            ++sampleIndex;
        }
        timeline.frameFeatures.push_back(timeline.samples[sampleIndex].features);
    }

    echoes::audio::RealtimeFeatures summary;
    double tempoAccum = 0.0;
    std::size_t tempoCount = 0;
    for (const auto& features : timeline.frameFeatures) {
        summary.bass += features.bass;
        summary.mid += features.mid;
        summary.treble += features.treble;
        summary.energy += features.energy;
        summary.beat = summary.beat || features.beat;
        summary.beatConfidence = std::max(summary.beatConfidence, features.beatConfidence);
        if (features.tempo > 0.0f) {
            tempoAccum += features.tempo;
            ++tempoCount;
        }
    }

    if (!timeline.frameFeatures.empty()) {
        const float inverseCount = 1.0f / static_cast<float>(timeline.frameFeatures.size());
        summary.bass *= inverseCount;
        summary.mid *= inverseCount;
        summary.treble *= inverseCount;
        summary.energy *= inverseCount;
    }
    summary.tempo = tempoCount == 0 ? 96.0f : static_cast<float>(tempoAccum / static_cast<double>(tempoCount));
    timeline.summary = summary;
    return timeline;
}

void applyStyleToCamera(echoes::camera::CameraDirector& cameraDirector, const StylePreset& preset) {
    cameraDirector.setOrbitRadius(preset.orbitRadius);
    cameraDirector.setOrbitSpeed(preset.orbitSpeed);
}

echoes::scene::SceneDescriptor makeReactiveScene(const echoes::scene::SceneDescriptor& baseScene,
                                                 const echoes::audio::RealtimeFeatures& features,
                                                 float timeSeconds) {
    auto scene = baseScene;
    scene.lighting.intensity =
        std::max(0.4f, baseScene.lighting.intensity * (1.0f + features.energy * 1.1f + (features.beat ? 0.2f : 0.0f)));

    scene.lighting.color.x = std::clamp(baseScene.lighting.color.x + features.treble * 0.25f, 0.0f, 1.4f);
    scene.lighting.color.y = std::clamp(baseScene.lighting.color.y + features.mid * 0.18f, 0.0f, 1.4f);
    scene.lighting.color.z = std::clamp(baseScene.lighting.color.z + features.bass * 0.22f, 0.0f, 1.4f);

    for (auto& primitive : scene.geometry) {
        primitive.rotation.y += timeSeconds * (0.2f + features.mid * 0.8f);
        primitive.scale.x *= 1.0f + features.bass * 0.12f;
        primitive.scale.y *= 1.0f + features.energy * 0.08f;
        primitive.scale.z *= 1.0f + features.bass * 0.12f;
        primitive.material.emissive = std::clamp(primitive.material.emissive + features.treble * 0.3f, 0.0f, 1.5f);
    }

    for (auto& emitter : scene.emitters) {
        emitter.emissionRate *= 1.0f + features.energy * 3.0f + features.bass * 1.5f;
        emitter.spread *= 1.0f + features.mid * 0.4f;
        emitter.lifetime *= 1.0f + features.treble * 0.15f;
        emitter.color.x = std::clamp(emitter.color.x + features.treble * 0.2f, 0.0f, 1.2f);
        emitter.color.y = std::clamp(emitter.color.y + features.mid * 0.15f, 0.0f, 1.2f);
        emitter.color.z = std::clamp(emitter.color.z + features.bass * 0.15f, 0.0f, 1.2f);
    }

    return scene;
}

StylePreset stylePresetFor(std::string_view style) {
    const auto normalized = toLower(std::string(style));
    if (normalized == "cyberpunk") {
        return {"cyberpunk_city", "HyperPulse", "neon-drenched skyline sprint", 10.5f, 0.9f, 2.2f};
    }
    if (normalized == "volcanic") {
        return {"volcanic_world", "Volcanic Forge", "eruptive lava storm panorama", 8.0f, 0.48f, 2.4f};
    }
    if (normalized == "nebula") {
        return {"nebula_space", "Nebula Bloom", "stellar cloud drift through prismatic gas", 11.5f, 0.4f, 2.6f};
    }
    if (normalized == "industrial") {
        return {"industrial_hellscape", "Industrial Grind", "molten foundry corridor with mechanical fury", 7.5f, 0.72f, 1.8f};
    }
    if (normalized == "dark_cinematic") {
        return {"dark_cathedral", "Gothic Shadow", "ancient moonlit cathedral aisles", 8.5f, 0.35f, 2.0f};
    }
    if (normalized == "fantasy") {
        return {"fantasy_realm", "Ethereal Dream", "soaring through floating crystal islands", 12.5f, 0.45f, 2.8f};
    }
    if (normalized == "dystopian") {
        return {"dystopian_wasteland", "Iron Dust", "drifting over rusted desert monoliths", 9.5f, 0.68f, 1.9f};
    }
    if (normalized == "alien_planet") {
        return {"alien_planet", "Exo Bloom", "walking the horizon of an alien biosphere", 10.0f, 0.52f, 2.4f};
    }
    return {"cosmic_blackhole", "Cosmic Wash", "gravitational orbit around a cinematic singularity", 9.0f, 0.55f, 2.0f};
}

class SceneDrivenFrameSource final : public echoes::export_::FrameCaptureSource {
public:
    SceneDrivenFrameSource(echoes::render::RenderPipeline& pipeline,
                           echoes::scene::SceneDescriptor scene,
                           echoes::camera::CameraDirector& cameraDirector,
                           AudioTimeline timeline,
                           float frameRate,
                           JobLogger& logger,
                           std::function<void(std::size_t)> progressCallback = {})
        : pipeline_(pipeline)
        , scene_(std::move(scene))
        , cameraDirector_(cameraDirector)
        , timeline_(std::move(timeline))
        , frameRate_(frameRate > 0.0f ? frameRate : 30.0f)
        , logger_(logger)
        , progressCallback_(std::move(progressCallback)) {
    }

    bool capture(uint32_t width, uint32_t height, std::vector<uint8_t>& outPixels) override {
        const auto& audioFeatures = timeline_.featureAt(frameIndex_);
        cameraDirector_.update(1.0f / frameRate_, toCameraFeatures(audioFeatures));

        if (frameIndex_ % 10 == 0) {
            logger_.info("frame rendering: index=" + std::to_string(frameIndex_) +
                         "/" + std::to_string(timeline_.totalFrames));
        }

        const float timeSeconds = static_cast<float>(frameIndex_) / frameRate_;
        echoes::render::SceneRenderRequest request;
        request.sceneName = scene_.name;
        request.scene = makeReactiveScene(scene_, audioFeatures, timeSeconds);
        request.camera = cameraDirector_.getTransform();
        request.audio = audioFeatures;
        request.target = {
            width,
            height,
            1,
            echoes::render::FrameBufferFormat::RGBA16F,
            echoes::render::FrameBufferFormat::DEPTH24,
            true
        };
        request.frameIndex = frameIndex_;
        request.timeSeconds = timeSeconds;

        const bool ok = pipeline_.renderToPixels(request, outPixels);
        ++frameIndex_;
        if (progressCallback_) {
            progressCallback_(std::min<std::size_t>(frameIndex_, timeline_.totalFrames));
        }
        return ok;
    }

    void reset() override {
        frameIndex_ = 0;
        cameraDirector_.reset();
    }

private:
    echoes::render::RenderPipeline& pipeline_;
    echoes::scene::SceneDescriptor scene_;
    echoes::camera::CameraDirector& cameraDirector_;
    AudioTimeline timeline_;
    float frameRate_ = 30.0f;
    uint32_t frameIndex_ = 0;
    JobLogger& logger_;
    std::function<void(std::size_t)> progressCallback_;
};

class EchoesHttpHost {
public:
    EchoesHttpHost()
        : masterAgent_(
              jobRegistry_,
              [this](const echoes::api::GenerateJobRequest& request) {
                  return processJob(request);
              },
              echoes::platform::loadRuntimeConfigFromEnvironment(std::filesystem::current_path() / "runtime"))
        , apiServer_(jobRegistry_, [this](echoes::api::GenerateJobRequest request) {
              masterAgent_.submit(std::move(request));
          }) {}

    bool start(uint16_t port) {
        if (!masterAgent_.start()) {
            return false;
        }
        if (!apiServer_.start(port)) {
            masterAgent_.stop();
            return false;
        }
        return true;
    }

    void stop() {
        {
            std::lock_guard lock(mutex_);
            stopRequested_ = true;
        }
        apiServer_.stop();
        masterAgent_.stop();
        cv_.notify_all();
    }

    void waitUntilStopped() {
        std::unique_lock lock(mutex_);
        cv_.wait(lock, [this] { return stopRequested_; });
    }

private:
    bool processJob(const echoes::api::GenerateJobRequest& request) {
        const auto uploadJobRoot = std::filesystem::current_path() / "jobs" / request.jobId;
        std::filesystem::create_directories(uploadJobRoot);
        const auto renderLogPath = uploadJobRoot / "render.log";
        JobLogger logger(renderLogPath);

        try {
            const auto jobRoot = std::filesystem::current_path() / "renders" / request.jobId;
            std::filesystem::create_directories(jobRoot);
            logger.info("job start: render_log=" + renderLogPath.string());
            jobRegistry_.markStatus(request.jobId, echoes::api::JobStatus::ANALYSIS);
            logger.info("audio loading: source=" + request.audioPath);

            const auto analysisAudioPath = prepareAudioForAnalysis(request.audioPath, &renderLogPath);
            if (!analysisAudioPath) {
                logger.error("audio loading failed: could not prepare analysis audio from " + request.audioPath);
                jobRegistry_.markFailed(request.jobId, "audio prepare failed");
                return false;
            }

            logger.info("audio loading ready: source=" + request.audioPath +
                        ", analysis=" + analysisAudioPath->string());

            auto wav = loadWavFile(*analysisAudioPath);
            if (!wav) {
                logger.error("audio loading failed: unable to read wav data from " + analysisAudioPath->string());
                jobRegistry_.markFailed(request.jobId, "audio load failed");
                return false;
            }
            logger.info("audio loading complete: samples=" + std::to_string(wav->monoSamples.size()) +
                        ", sample_rate=" + std::to_string(wav->sampleRate));

            auto renderProfile = loadRenderProfile();
            if (request.requestedWidth > 0) {
                renderProfile.width = std::max<uint32_t>(640, request.requestedWidth);
            }
            if (request.requestedHeight > 0) {
                renderProfile.height = std::max<uint32_t>(360, request.requestedHeight);
            }

            auto timeline = buildAudioTimeline(*wav, renderProfile.fps);
            if (request.requestedDurationSeconds > 0.0) {
                timeline.durationSeconds = std::max(1.0, request.requestedDurationSeconds);
                timeline.totalFrames = std::max<std::size_t>(
                    1,
                    static_cast<std::size_t>(std::ceil(
                        timeline.durationSeconds * static_cast<double>(std::max<uint32_t>(renderProfile.fps, 1)))));
            }
            const auto features = timeline.summary;
            logger.info("audio analysis complete: bass=" + std::to_string(features.bass) +
                        ", mid=" + std::to_string(features.mid) +
                        ", treble=" + std::to_string(features.treble) +
                        ", energy=" + std::to_string(features.energy) +
                        ", beat=" + std::string(features.beat ? "true" : "false") +
                        ", tempo=" + std::to_string(features.tempo));
            logger.info("audio timeline complete: duration=" + std::to_string(timeline.durationSeconds) +
                        "s, total_frames=" + std::to_string(timeline.totalFrames) +
                        ", fps=" + std::to_string(renderProfile.fps));
            logger.info("render request overrides: width=" + std::to_string(renderProfile.width) +
                        ", height=" + std::to_string(renderProfile.height) +
                        ", requested_duration=" + std::to_string(request.requestedDurationSeconds));

            const auto autoPrompt = echoes::ai_prompt::PromptDirector::generate_prompt_from_audio(
                toPromptFeatures(features));
            const auto stylePreset = stylePresetFor(request.style);
            const auto sceneId = stylePreset.sceneId.empty() ? autoPrompt.scene.id : stylePreset.sceneId;
            jobRegistry_.markStatus(request.jobId, echoes::api::JobStatus::SCENE_GENERATION);
            logger.info("scene generation start: scene_id=" + sceneId);
            const auto scene = echoes::scene::SceneFactory::createScene(sceneId);
            if (!scene) {
                logger.error("scene generation failed: could not create " + sceneId);
                jobRegistry_.markFailed(request.jobId, "scene creation failed");
                return false;
            }
            logger.info("scene generation complete: scene_name=" + scene->name);

            const std::string prompt = request.prompt.empty()
                ? "Render " + stylePreset.scenePrompt + " with " + stylePreset.styleName +
                    " treatment, guided by " + autoPrompt.prompt
                : request.prompt;
            const std::string styleName = stylePreset.styleName.empty() ? autoPrompt.style : stylePreset.styleName;

            echoes::render::RenderPipeline renderPipeline(echoes::render::Backend::Vulkan);
            logger.info("renderer start: backend=Vulkan");
            if (!renderPipeline.initialize()) {
                logger.error("renderer start failed: pipeline initialization returned false");
                jobRegistry_.markFailed(request.jobId, "render initialization failed");
                return false;
            }
            logger.info("renderer start complete");

            echoes::render::SceneRenderer sceneRenderer(renderPipeline);
            echoes::camera::CameraDirector cameraDirector;
            echoes::ai::AIVideoPipeline aiPipeline(2);
            echoes::export_::ExportJobManager exportManager;

            if (!scene->cameraPath.keyframes.empty()) {
                cameraDirector.setBasePosition(toCameraVec3(scene->cameraPath.keyframes.front().position));
                cameraDirector.setTargetPosition(toCameraVec3(scene->cameraPath.keyframes.front().target));
            }
            applyStyleToCamera(cameraDirector, stylePreset);
            cameraDirector.update(1.0f / static_cast<float>(renderProfile.fps), toCameraFeatures(features));

            sceneRenderer.scheduleScene({
                scene->name,
                makeReactiveScene(*scene, timeline.featureAt(0), 0.0f),
                cameraDirector.getTransform(),
                timeline.featureAt(0),
                {renderProfile.width, renderProfile.height, 1, echoes::render::FrameBufferFormat::RGBA16F,
                 echoes::render::FrameBufferFormat::DEPTH24, true},
                0,
                0.0f
            });
            sceneRenderer.renderFrame();
            logger.info("scene render bootstrap complete");

            logger.info("frame generation start: prompt=\"" + prompt + "\", style=" + styleName);
            echoes::ai::SceneVideoConfig sceneVideoConfig;
            sceneVideoConfig.resolution.width = renderProfile.width;
            sceneVideoConfig.resolution.height = renderProfile.height;
            sceneVideoConfig.durationSeconds = std::max(1.0, timeline.durationSeconds);
            sceneVideoConfig.framesPerSecond = renderProfile.fps;
            const auto pipelineJobId = aiPipeline.generate_scene_video(
                prompt,
                request.audioPath,
                styleName,
                sceneVideoConfig);
            aiPipeline.apply_style_transfer(pipelineJobId, {0.82, styleName, true});
            aiPipeline.generate_frames(pipelineJobId, timeline.totalFrames);
            aiPipeline.compose_final_video(pipelineJobId);

            std::shared_ptr<echoes::ai::JobContext> pipelineJob;
            bool pipelineReady = false;
            for (int attempt = 0; attempt < 500; ++attempt) {
                pipelineJob = aiPipeline.get_job(pipelineJobId);
                if (pipelineJob) {
                    std::lock_guard lock(pipelineJob->mutex);
                    if (pipelineJob->state == echoes::ai::JobState::Completed ||
                        pipelineJob->state == echoes::ai::JobState::Failed ||
                        !pipelineJob->finalVideo.empty()) {
                        pipelineReady = true;
                        break;
                    }
                }
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
            }

            if (!pipelineJob || !pipelineReady) {
                logger.error("frame generation failed: ai pipeline timeout");
                jobRegistry_.markFailed(request.jobId, "ai pipeline timeout");
                aiPipeline.shutdown();
                renderPipeline.shutdown();
                return false;
            }

            {
                std::lock_guard lock(pipelineJob->mutex);
                if (pipelineJob->state == echoes::ai::JobState::Failed) {
                    logger.error("frame generation failed: ai pipeline returned failed state");
                    jobRegistry_.markFailed(request.jobId, "ai pipeline failed");
                    aiPipeline.shutdown();
                    renderPipeline.shutdown();
                    return false;
                }
            }
            logger.info("frame generation complete: ai job ready");

            echoes::export_::ExportSettings exportSettings;
            exportSettings.resolution.width = renderProfile.width;
            exportSettings.resolution.height = renderProfile.height;
            exportSettings.frameRate = renderProfile.fps;
            exportSettings.keyframeInterval = renderProfile.keyframeInterval;
            exportSettings.workingDirectory = jobRoot;
            exportSettings.outputFile = (jobRoot / "video.mp4").string();
            jobRegistry_.configureRender(
                request.jobId,
                timeline.durationSeconds,
                timeline.totalFrames,
                exportSettings.resolution.width,
                exportSettings.resolution.height,
                exportSettings.frameRate);

            SceneDrivenFrameSource frameSource(
                renderPipeline,
                *scene,
                cameraDirector,
                timeline,
                static_cast<float>(exportSettings.frameRate),
                logger,
                [this, jobId = request.jobId](std::size_t renderedFrames) {
                    jobRegistry_.updateRenderedFrames(jobId, renderedFrames);
                });
            frameSource.reset();
            jobRegistry_.markStatus(request.jobId, echoes::api::JobStatus::RENDERING);
            logger.info("frame export start: exporting scene-driven frames to " + jobRoot.string());
            const auto exportJob = echoes::export_::export_mp4(exportManager, frameSource, exportSettings, timeline.totalFrames);
            if (exportJob.status != "ready") {
                logger.error("frame generation failed: " + exportJob.message);
                jobRegistry_.markFailed(request.jobId, exportJob.message);
                aiPipeline.shutdown();
                renderPipeline.shutdown();
                return false;
            }
            logger.info("frame export complete: " + std::to_string(timeline.totalFrames) + " frames captured");

            const auto ffmpegPath = findFfmpegExecutable();
            if (!ffmpegPath) {
                logger.error("encoding failed: ffmpeg executable not found");
                jobRegistry_.markFailed(request.jobId, "ffmpeg not found");
                aiPipeline.shutdown();
                renderPipeline.shutdown();
                return false;
            }

            const auto framesPattern = jobRoot / "frames" / "frame_%04d.ppm";
            const auto outputVideo = jobRoot / "video.mp4";
            std::filesystem::remove(outputVideo);

            std::wostringstream ffmpegCommand;
            ffmpegCommand << L"\"" << ffmpegPath->wstring() << L"\""
                          << L" -y"
                          << L" -framerate " << exportSettings.frameRate
                          << L" -start_number 0"
                          << L" -i \"" << framesPattern.wstring() << L"\""
                          << L" -c:v libx264"
                          << L" -pix_fmt yuv420p"
                          << L" -crf 20"
                          << L" -profile:v high"
                          << L" -movflags +faststart"
                          << L" -preset medium"
                          << L" -r " << exportSettings.frameRate
                          << L" -g " << exportSettings.keyframeInterval
                          << L" \"" << outputVideo.wstring() << L"\"";

            jobRegistry_.markStatus(request.jobId, echoes::api::JobStatus::ENCODING);
            DWORD ffmpegExitCode = 1;
            logger.info("encoding start: output=" + outputVideo.string());
            const bool launched = runProcess(ffmpegCommand.str(), ffmpegExitCode, &renderLogPath);
            if (!launched || ffmpegExitCode != 0 || !std::filesystem::exists(outputVideo)) {
                logger.error("encoding failed: ffmpeg exit code=" + std::to_string(ffmpegExitCode));
                jobRegistry_.markFailed(request.jobId, "ffmpeg encode failed");
                aiPipeline.shutdown();
                renderPipeline.shutdown();
                return false;
            }
            logger.info("encoding complete: video=" + outputVideo.string());

            const auto transform = cameraDirector.getTransform();
            logger.info("job complete: scene=" + scene->name +
                        ", style=" + styleName +
                        ", camera=(" + std::to_string(transform.position.x) + ", " +
                        std::to_string(transform.position.y) + ", " +
                        std::to_string(transform.position.z) + ")");

            jobRegistry_.markStatus(request.jobId, echoes::api::JobStatus::FINISHED);
            jobRegistry_.markReady(request.jobId, outputVideo.string());
            aiPipeline.shutdown();
            renderPipeline.shutdown();
            return true;
        } catch (const std::exception& ex) {
            logger.error("job failed with exception: " + std::string(ex.what()));
            jobRegistry_.markFailed(request.jobId, ex.what());
            return false;
        }
    }

    echoes::api::JobRegistry jobRegistry_;
    echoes::platform::MasterAgent masterAgent_;
    echoes::api::ApiServer apiServer_;
    std::mutex mutex_;
    std::condition_variable cv_;
    bool stopRequested_ = false;
};

EchoesHttpHost* gHost = nullptr;

#ifdef _WIN32
BOOL WINAPI onConsoleSignal(DWORD signal) {
    switch (signal) {
        case CTRL_C_EVENT:
        case CTRL_BREAK_EVENT:
        case CTRL_CLOSE_EVENT:
        case CTRL_SHUTDOWN_EVENT:
            if (gHost != nullptr) {
                gHost->stop();
            }
            return TRUE;
        default:
            return FALSE;
    }
}
#endif

} // namespace

int main() {
    std::cout << "ECHoesEngine PRO HTTP server bootstrap\n";

    echoes::opencl::OpenCLContext openclContext;
    const auto opencl = openclContext.initialize();
    if (opencl.initialized) {
        std::cout << "EchoesEngine OpenCL device initialized: "
                  << opencl.device.deviceName
                  << " | compute units=" << opencl.device.computeUnits
                  << " | kernel dir=" << opencl.kernelDirectory
                  << "\n";
        std::cout << "OpenCL device detected\n";
        if (opencl.selfTestPassed) {
            std::cout << "GPU compute test successful\n";
        }
    } else {
        std::cerr << "EchoesEngine OpenCL initialization skipped: " << opencl.message << "\n";
    }

    EchoesHttpHost host;
    gHost = &host;

#ifdef _WIN32
    SetConsoleCtrlHandler(onConsoleSignal, TRUE);
#endif

    constexpr uint16_t port = 8080;
    if (!host.start(port)) {
        std::cerr << "Failed to start HTTP server on http://127.0.0.1:" << port << "\n";
        return 1;
    }

    std::cout << "HTTP API listening on http://127.0.0.1:" << port << "\n";
    std::cout << "Endpoints: POST /generate, GET /status/{job_id}, GET /video/{job_id}, GET /log/{job_id}\n";

    host.waitUntilStopped();
    gHost = nullptr;
    return 0;
}
