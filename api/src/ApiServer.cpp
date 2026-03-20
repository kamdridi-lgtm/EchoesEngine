#include "EchoesEngine/api/ApiServer.h"

#include "EchoesEngine/api/Router.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cctype>
#include <cstdint>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <optional>
#include <sstream>
#include <string_view>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#pragma comment(lib, "Ws2_32.lib")
#endif

namespace echoes::api {

namespace {

std::string statusToString(JobStatus status) {
    switch (status) {
        case JobStatus::Pending:
            return "queued";
        case JobStatus::Processing:
            return "generating";
        case JobStatus::ANALYSIS:
            return "generating";
        case JobStatus::SCENE_GENERATION:
            return "generating";
        case JobStatus::RENDERING:
            return "generating";
        case JobStatus::ENCODING:
            return "encoding";
        case JobStatus::FINISHED:
            return "finished";
        case JobStatus::Ready:
            return "finished";
        case JobStatus::Failed:
            return "failed";
    }
    return "failed";
}

std::string stageToString(JobStatus status) {
    switch (status) {
        case JobStatus::Pending:
            return "queued";
        case JobStatus::Processing:
            return "queued";
        case JobStatus::ANALYSIS:
            return "analysis";
        case JobStatus::SCENE_GENERATION:
            return "scene_generation";
        case JobStatus::RENDERING:
            return "rendering";
        case JobStatus::ENCODING:
            return "encoding";
        case JobStatus::FINISHED:
        case JobStatus::Ready:
            return "finished";
        case JobStatus::Failed:
            return "failed";
    }
    return "failed";
}

double progressToPercent(const JobInfo& job) {
    switch (job.status) {
        case JobStatus::Pending:
            return 5.0;
        case JobStatus::Processing:
            return 10.0;
        case JobStatus::ANALYSIS:
            return 20.0;
        case JobStatus::SCENE_GENERATION:
            return 40.0;
        case JobStatus::RENDERING:
            if (job.totalFrames == 0) {
                return 60.0;
            }
            return 40.0 + (static_cast<double>(job.renderedFrames) / static_cast<double>(job.totalFrames)) * 45.0;
        case JobStatus::ENCODING:
            return 92.0;
        case JobStatus::FINISHED:
        case JobStatus::Ready:
            return 100.0;
        case JobStatus::Failed:
            return job.renderedFrames > 0 ? 95.0 : 0.0;
    }
    return 0.0;
}

std::string formatTimestamp(const std::chrono::system_clock::time_point& tp) {
    const auto timeT = std::chrono::system_clock::to_time_t(tp);
    std::tm utcTime{};
#if defined(_WIN32)
    gmtime_s(&utcTime, &timeT);
#else
    gmtime_r(&timeT, &utcTime);
#endif
    std::ostringstream oss;
    oss << std::put_time(&utcTime, "%Y-%m-%dT%H:%M:%SZ");
    return oss.str();
}

std::string toLower(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return value;
}

std::string trim(std::string value) {
    auto notSpace = [](unsigned char c) { return !std::isspace(c); };
    value.erase(value.begin(), std::find_if(value.begin(), value.end(), notSpace));
    value.erase(std::find_if(value.rbegin(), value.rend(), notSpace).base(), value.end());
    return value;
}

bool isValidStyle(std::string_view style) {
    static constexpr std::array<std::string_view, 11> kAllowed{
        "nebula", "aurora", "galaxy", "cosmic", "cyberpunk",
        "volcanic", "industrial", "dark_cinematic", "fantasy",
        "dystopian", "alien_planet"
    };
    return std::find(kAllowed.begin(), kAllowed.end(), style) != kAllowed.end();
}

bool isAllowedOrigin(std::string_view origin) {
    static constexpr std::array<std::string_view, 6> kAllowedOrigins{
        "https://visual-kamdridi.com",
        "http://visual-kamdridi.com",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8082",
        "http://127.0.0.1:8082"
    };
    return std::find(kAllowedOrigins.begin(), kAllowedOrigins.end(), origin) != kAllowedOrigins.end();
}

std::string sanitizeFileName(std::string_view fileName) {
    std::string clean;
    clean.reserve(fileName.size());
    for (char ch : fileName) {
        if (std::isalnum(static_cast<unsigned char>(ch)) || ch == '.' || ch == '_' || ch == '-') {
            clean.push_back(ch);
        }
    }
    return clean.empty() ? "input.wav" : clean;
}

std::string uploadExtensionFor(std::string_view fileName) {
    const auto extension = std::filesystem::path(std::string(fileName)).extension().string();
    if (extension.empty()) {
        return ".mp3";
    }
    return toLower(extension);
}

std::optional<std::string> findHeader(const std::unordered_map<std::string, std::string>& headers,
                                      std::string_view key) {
    const auto normalizedKey = toLower(std::string(key));
    for (const auto& [headerName, value] : headers) {
        if (toLower(headerName) == normalizedKey) {
            return value;
        }
    }
    return std::nullopt;
}

std::string corsOriginFor(const HttpRequest& request) {
    const auto origin = findHeader(request.headers, "origin");
    if (origin) {
        return *origin;
    }
    return "*";
}

void appendCorsHeaders(const HttpRequest& request, HttpResponse& response) {
    response.headers.emplace_back("Access-Control-Allow-Origin", corsOriginFor(request));
    response.headers.emplace_back("Access-Control-Allow-Methods", "POST, GET, OPTIONS");
    response.headers.emplace_back("Access-Control-Allow-Headers", "Content-Type");
    response.headers.emplace_back("Access-Control-Max-Age", "600");
    response.headers.emplace_back("Vary", "Origin");

    const auto privateNetwork = findHeader(request.headers, "access-control-request-private-network");
    if (privateNetwork && toLower(*privateNetwork) == "true") {
        response.headers.emplace_back("Access-Control-Allow-Private-Network", "true");
    }
}

struct MultipartGeneratePayload {
    std::string prompt;
    std::string style;
    std::string fileName;
    std::string audioBytes;
};

std::optional<MultipartGeneratePayload> parseMultipartGenerate(const HttpRequest& request) {
    const auto contentType = findHeader(request.headers, "content-type");
    if (!contentType || contentType->find("multipart/form-data") == std::string::npos) {
        return std::nullopt;
    }

    const auto boundaryPos = contentType->find("boundary=");
    if (boundaryPos == std::string::npos) {
        return std::nullopt;
    }

    const std::string boundary = "--" + contentType->substr(boundaryPos + 9);
    MultipartGeneratePayload payload;
    std::size_t cursor = 0;

    while (true) {
        const auto start = request.body.find(boundary, cursor);
        if (start == std::string::npos) {
            break;
        }

        const auto headerStart = start + boundary.size();
        if (request.body.compare(headerStart, 2, "--") == 0) {
            break;
        }

        const auto partHeaderBegin = request.body.find("\r\n", headerStart);
        const auto partHeaderEnd = request.body.find("\r\n\r\n", partHeaderBegin == std::string::npos ? headerStart : partHeaderBegin + 2);
        if (partHeaderEnd == std::string::npos) {
            break;
        }

        const auto bodyStart = partHeaderEnd + 4;
        const auto nextBoundary = request.body.find(boundary, bodyStart);
        if (nextBoundary == std::string::npos) {
            break;
        }

        const auto bodyEnd = nextBoundary >= 2 ? nextBoundary - 2 : nextBoundary;
        const std::string headers = request.body.substr(partHeaderBegin + 2, partHeaderEnd - (partHeaderBegin + 2));
        const std::string body = request.body.substr(bodyStart, bodyEnd - bodyStart);

        const auto namePos = headers.find("name=\"");
        if (namePos == std::string::npos) {
            cursor = nextBoundary;
            continue;
        }

        const auto nameStart = namePos + 6;
        const auto nameEnd = headers.find('"', nameStart);
        if (nameEnd == std::string::npos) {
            cursor = nextBoundary;
            continue;
        }

        const auto fieldName = headers.substr(nameStart, nameEnd - nameStart);
        if (fieldName == "style") {
            payload.style = trim(body);
        } else if (fieldName == "prompt") {
            payload.prompt = trim(body);
        } else if (fieldName == "audio") {
            const auto fileNamePos = headers.find("filename=\"");
            if (fileNamePos != std::string::npos) {
                const auto fileStart = fileNamePos + 10;
                const auto fileEnd = headers.find('"', fileStart);
                if (fileEnd != std::string::npos) {
                    payload.fileName = sanitizeFileName(headers.substr(fileStart, fileEnd - fileStart));
                }
            }
            payload.audioBytes = body;
        }

        cursor = nextBoundary;
    }

    if (payload.style.empty() || payload.audioBytes.empty()) {
        return std::nullopt;
    }

    if (payload.fileName.empty()) {
        payload.fileName = "input.wav";
    }

    return payload;
}

std::string readBinaryFile(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        return {};
    }
    return std::string((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
}

std::string readTextFile(const std::filesystem::path& path) {
    std::ifstream input(path);
    if (!input) {
        return {};
    }
    return std::string((std::istreambuf_iterator<char>(input)), std::istreambuf_iterator<char>());
}

std::optional<std::string> defaultAudioPath() {
    static constexpr std::array<std::string_view, 3> kCandidates{
        "jobs/synthetic-test-0p3s.wav",
        "jobs/synthetic-test-1s.wav",
        "jobs/synthetic-test.wav"
    };

    for (const auto candidate : kCandidates) {
        const auto path = std::filesystem::current_path() / candidate;
        if (std::filesystem::exists(path)) {
            return path.string();
        }
    }
    return std::nullopt;
}

std::string buildHttpResponse(const HttpResponse& response) {
    std::ostringstream oss;
    oss << "HTTP/1.1 " << static_cast<int>(response.status) << " " << statusMessage(response.status) << "\r\n";

    bool hasContentType = false;
    for (const auto& [name, value] : response.headers) {
        if (toLower(name) == "content-type") {
            hasContentType = true;
        }
        oss << name << ": " << value << "\r\n";
    }

    if (!hasContentType) {
        oss << "Content-Type: application/json\r\n";
    }
    oss << "Content-Length: " << response.body.size() << "\r\n";
    oss << "Connection: close\r\n\r\n";
    oss << response.body;
    return oss.str();
}

HttpRequest parseHttpRequest(std::string_view raw) {
    HttpRequest request;
    const auto requestLineEnd = raw.find("\r\n");
    if (requestLineEnd == std::string_view::npos) {
        return request;
    }

    const auto requestLine = raw.substr(0, requestLineEnd);
    const auto methodEnd = requestLine.find(' ');
    const auto pathEnd = requestLine.find(' ', methodEnd == std::string_view::npos ? 0 : methodEnd + 1);
    if (methodEnd == std::string_view::npos || pathEnd == std::string_view::npos) {
        return request;
    }

    request.method = methodFromString(requestLine.substr(0, methodEnd));
    request.path = std::string(requestLine.substr(methodEnd + 1, pathEnd - methodEnd - 1));

    const auto headersEnd = raw.find("\r\n\r\n");
    if (headersEnd == std::string_view::npos) {
        return request;
    }

    std::size_t headerStart = requestLineEnd + 2;
    while (headerStart < headersEnd) {
        const auto headerEnd = raw.find("\r\n", headerStart);
        if (headerEnd == std::string_view::npos || headerEnd > headersEnd) {
            break;
        }
        const auto header = raw.substr(headerStart, headerEnd - headerStart);
        const auto colon = header.find(':');
        if (colon != std::string_view::npos) {
            request.headers.emplace(
                std::string(header.substr(0, colon)),
                trim(std::string(header.substr(colon + 1))));
        }
        headerStart = headerEnd + 2;
    }

    request.body = std::string(raw.substr(headersEnd + 4));
    return request;
}

#ifdef _WIN32
std::string readSocketRequest(SOCKET client) {
    std::string data;
    data.reserve(65536);
    char buffer[8192];
    int contentLength = -1;
    std::size_t headersEnd = std::string::npos;

    while (true) {
        const int received = recv(client, buffer, static_cast<int>(sizeof(buffer)), 0);
        if (received <= 0) {
            break;
        }

        data.append(buffer, buffer + received);

        if (headersEnd == std::string::npos) {
            headersEnd = data.find("\r\n\r\n");
            if (headersEnd != std::string::npos) {
                const auto headerBlock = data.substr(0, headersEnd);
                const auto lengthPos = toLower(headerBlock).find("content-length:");
                if (lengthPos != std::string::npos) {
                    const auto lineEnd = headerBlock.find("\r\n", lengthPos);
                    const auto value = trim(headerBlock.substr(lengthPos + 15, lineEnd - (lengthPos + 15)));
                    contentLength = std::atoi(value.c_str());
                } else {
                    break;
                }
            }
        }

        if (headersEnd != std::string::npos && contentLength >= 0) {
            const auto currentBody = data.size() - (headersEnd + 4);
            if (currentBody >= static_cast<std::size_t>(contentLength)) {
                break;
            }
        }
    }

    return data;
}
#endif

} // namespace

struct ApiServer::Impl {
    JobRegistry& registry;
    JobSubmitter submitter;
    Router router;
    std::atomic<bool> running{false};
    std::thread serverThread;
    uint16_t listenPort = 0;
#ifdef _WIN32
    SOCKET listenSocket = INVALID_SOCKET;
#endif

    explicit Impl(JobRegistry& jobRegistry, JobSubmitter jobSubmitter)
        : registry(jobRegistry)
        , submitter(std::move(jobSubmitter)) {
        router.addRoute(HttpMethod::Post, "/generate", [this](const HttpRequest& request,
                                                              const Router::Params&) {
            std::string prompt;
            std::string audioPath;
            std::string style;
            std::string savedUploadPath;

            if (const auto multipart = parseMultipartGenerate(request)) {
                style = toLower(multipart->style);
                if (!isValidStyle(style)) {
                    return HttpResponse{HttpStatus::BadRequest, "{\"error\":\"invalid style\"}", {{"Content-Type", "application/json"}}};
                }

                const auto jobId = registry.createJob(multipart->prompt, "", style, 0.0, 1280, 720);
                const auto jobRoot = std::filesystem::current_path() / "jobs" / jobId;
                std::filesystem::create_directories(jobRoot);
                const auto uploadPath = jobRoot / ("audio" + uploadExtensionFor(multipart->fileName));
                std::ofstream output(uploadPath, std::ios::binary);
                output.write(multipart->audioBytes.data(), static_cast<std::streamsize>(multipart->audioBytes.size()));
                output.close();

                if (submitter) {
                    std::thread(submitter, GenerateJobRequest{
                        jobId,
                        multipart->prompt,
                        uploadPath.string(),
                        style
                    }).detach();
                }

                GenerateResponse response{jobId};
                return HttpResponse{HttpStatus::Ok, response.toJson(), {{"Content-Type", "application/json"}}};
            }

            const auto payload = GenerateRequest::fromJson(request.body);
            if (!payload) {
                return HttpResponse{HttpStatus::BadRequest, "{\"error\":\"invalid payload\"}", {{"Content-Type", "application/json"}}};
            }

            style = toLower(payload->style);
            if (!isValidStyle(style)) {
                return HttpResponse{HttpStatus::BadRequest, "{\"error\":\"invalid style\"}", {{"Content-Type", "application/json"}}};
            }

            const auto resolvedAudio = payload->audio.empty() ? defaultAudioPath() : std::optional<std::string>{payload->audio};
            if (!resolvedAudio) {
                return HttpResponse{HttpStatus::BadRequest, "{\"error\":\"missing audio and no default test audio found\"}", {{"Content-Type", "application/json"}}};
            }

            const auto requestedWidth = payload->width == 0 ? 1280u : payload->width;
            const auto requestedHeight = payload->height == 0 ? 720u : payload->height;
            const auto jobId = registry.createJob(
                payload->prompt,
                *resolvedAudio,
                style,
                payload->durationSeconds,
                requestedWidth,
                requestedHeight);
            if (submitter) {
                std::thread(submitter, GenerateJobRequest{
                    jobId,
                    payload->prompt,
                    *resolvedAudio,
                    style,
                    payload->durationSeconds,
                    requestedWidth,
                    requestedHeight
                }).detach();
            }

            GenerateResponse response{jobId};
            return HttpResponse{HttpStatus::Ok, response.toJson(), {{"Content-Type", "application/json"}}};
        });

        router.addRoute(HttpMethod::Get, "/status/{job_id}", [this](const HttpRequest&,
                                                                     const Router::Params& params) {
            const auto it = params.find("job_id");
            if (it == params.end()) {
                return HttpResponse{HttpStatus::BadRequest, "{\"error\":\"missing job id\"}", {{"Content-Type", "application/json"}}};
            }

            const auto job = registry.getJob(it->second);
            if (!job) {
                return HttpResponse{HttpStatus::NotFound, "{\"error\":\"job not found\"}", {{"Content-Type", "application/json"}}};
            }

            StatusResponse response{
                job->jobId,
                statusToString(job->status),
                stageToString(job->status),
                progressToPercent(*job),
                job->videoUri,
                job->failureReason
            };

            return HttpResponse{HttpStatus::Ok, response.toJson(), {{"Content-Type", "application/json"}}};
        });

        router.addRoute(HttpMethod::Get, "/video/{job_id}", [this](const HttpRequest&,
                                                                    const Router::Params& params) {
            const auto it = params.find("job_id");
            if (it == params.end()) {
                return HttpResponse{HttpStatus::BadRequest, "{\"error\":\"missing job id\"}", {{"Content-Type", "application/json"}}};
            }

            const auto job = registry.getJob(it->second);
            if (!job) {
                return HttpResponse{HttpStatus::NotFound, "{\"error\":\"job not found\"}", {{"Content-Type", "application/json"}}};
            }

            if ((job->status != JobStatus::Ready && job->status != JobStatus::FINISHED) ||
                job->videoUri.empty() || !std::filesystem::exists(job->videoUri)) {
                return HttpResponse{HttpStatus::NotFound, "{\"error\":\"video not ready\"}", {{"Content-Type", "application/json"}}};
            }

            auto fileData = readBinaryFile(job->videoUri);
            if (fileData.empty()) {
                return HttpResponse{HttpStatus::InternalServerError, "{\"error\":\"video unavailable\"}", {{"Content-Type", "application/json"}}};
            }

            return HttpResponse{
                HttpStatus::Ok,
                std::move(fileData),
                {
                    {"Content-Type", "video/mp4"},
                    {"Content-Disposition", "inline; filename=\"video.mp4\""}
                }
            };
        });

        router.addRoute(HttpMethod::Get, "/log/{job_id}", [this](const HttpRequest&,
                                                                  const Router::Params& params) {
            const auto it = params.find("job_id");
            if (it == params.end()) {
                return HttpResponse{HttpStatus::BadRequest, "{\"error\":\"missing job id\"}", {{"Content-Type", "application/json"}}};
            }

            const auto job = registry.getJob(it->second);
            if (!job) {
                return HttpResponse{HttpStatus::NotFound, "{\"error\":\"job not found\"}", {{"Content-Type", "application/json"}}};
            }

            const auto logPath = std::filesystem::current_path() / "jobs" / it->second / "render.log";
            if (!std::filesystem::exists(logPath)) {
                return HttpResponse{HttpStatus::NotFound, "{\"error\":\"log not ready\"}", {{"Content-Type", "application/json"}}};
            }

            auto logData = readTextFile(logPath);
            if (logData.empty() && std::filesystem::file_size(logPath) > 0) {
                return HttpResponse{HttpStatus::InternalServerError, "{\"error\":\"log unavailable\"}", {{"Content-Type", "application/json"}}};
            }

            return HttpResponse{
                HttpStatus::Ok,
                std::move(logData),
                {{"Content-Type", "text/plain; charset=utf-8"}}
            };
        });
    }

    HttpResponse handleRequest(const HttpRequest& request) const {
        if (request.method == HttpMethod::Options) {
            HttpResponse response{HttpStatus::Ok, {}, {{"Content-Type", "text/plain"}}};
            appendCorsHeaders(request, response);
            return response;
        }

        auto response = router.dispatch(request);
        appendCorsHeaders(request, response);
        return response;
    }

#ifdef _WIN32
    void serve() {
        while (running.load()) {
            sockaddr_in clientAddr{};
            int clientSize = sizeof(clientAddr);
            const SOCKET client = accept(listenSocket, reinterpret_cast<sockaddr*>(&clientAddr), &clientSize);
            if (client == INVALID_SOCKET) {
                if (!running.load()) {
                    break;
                }
                continue;
            }

            const auto requestText = readSocketRequest(client);
            const auto request = parseHttpRequest(requestText);
            const auto response = handleRequest(request);
            const auto serialized = buildHttpResponse(response);
            send(client, serialized.data(), static_cast<int>(serialized.size()), 0);
            closesocket(client);
        }
    }
#endif
};

ApiServer::ApiServer(JobRegistry& registry, JobSubmitter submitter)
    : impl_(std::make_unique<Impl>(registry, std::move(submitter))) {}

ApiServer::~ApiServer() {
    stop();
}

HttpResponse ApiServer::handleRequest(const HttpRequest& request) const {
    return impl_->handleRequest(request);
}

bool ApiServer::start(uint16_t port) {
#ifdef _WIN32
    if (impl_->running.exchange(true)) {
        return true;
    }

    WSADATA wsaData{};
    if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0) {
        impl_->running = false;
        return false;
    }

    impl_->listenSocket = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (impl_->listenSocket == INVALID_SOCKET) {
        WSACleanup();
        impl_->running = false;
        return false;
    }

    sockaddr_in address{};
    address.sin_family = AF_INET;
    address.sin_port = htons(port);
    inet_pton(AF_INET, "127.0.0.1", &address.sin_addr);

    if (bind(impl_->listenSocket, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == SOCKET_ERROR ||
        listen(impl_->listenSocket, SOMAXCONN) == SOCKET_ERROR) {
        closesocket(impl_->listenSocket);
        impl_->listenSocket = INVALID_SOCKET;
        WSACleanup();
        impl_->running = false;
        return false;
    }

    impl_->listenPort = port;
    impl_->serverThread = std::thread([this]() { impl_->serve(); });
    return true;
#else
    (void)port;
    return false;
#endif
}

void ApiServer::stop() {
#ifdef _WIN32
    if (!impl_->running.exchange(false)) {
        return;
    }

    if (impl_->listenSocket != INVALID_SOCKET) {
        const auto socketToClose = impl_->listenSocket;
        impl_->listenSocket = INVALID_SOCKET;
        closesocket(socketToClose);

        SOCKET wakeSocket = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (wakeSocket != INVALID_SOCKET) {
            sockaddr_in wakeAddr{};
            wakeAddr.sin_family = AF_INET;
            wakeAddr.sin_port = htons(impl_->listenPort);
            inet_pton(AF_INET, "127.0.0.1", &wakeAddr.sin_addr);
            connect(wakeSocket, reinterpret_cast<sockaddr*>(&wakeAddr), sizeof(wakeAddr));
            closesocket(wakeSocket);
        }
    }

    if (impl_->serverThread.joinable()) {
        impl_->serverThread.join();
    }

    WSACleanup();
#endif
}

bool ApiServer::isRunning() const noexcept {
    return impl_->running.load();
}

uint16_t ApiServer::port() const noexcept {
    return impl_->listenPort;
}

void ApiServer::setJobSubmitter(JobSubmitter submitter) {
    impl_->submitter = std::move(submitter);
}

} // namespace echoes::api
