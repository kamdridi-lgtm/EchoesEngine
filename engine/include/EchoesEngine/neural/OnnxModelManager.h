#pragma once

#include <array>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <string>
#include <system_error>
#include <utility>
#include <vector>

namespace echoes::neural {

enum class ModelEvidenceStatus { PASS, BLOCKED, DORMANT };

inline const char* toString(ModelEvidenceStatus status) noexcept {
    switch (status) {
        case ModelEvidenceStatus::PASS: return "PASS";
        case ModelEvidenceStatus::DORMANT: return "DORMANT";
        default: return "BLOCKED";
    }
}

struct OnnxModelDescriptor {
    std::string id;
    std::string purpose;
    std::filesystem::path relativePath;
    std::string expectedSha256;
    std::uintmax_t expectedSizeBytes = 0;
    double minimumVramGiB = 0.0;
    int qualityScore = 1;
    int maxConcurrency = 1;
    bool commercialUseAllowed = false;
    bool enabled = true;
    std::vector<std::string> providers;
    std::vector<std::string> precisions;
    std::vector<std::string> capabilities;
};

struct OnnxModelEvidence {
    ModelEvidenceStatus status = ModelEvidenceStatus::BLOCKED;
    OnnxModelDescriptor descriptor;
    std::filesystem::path resolvedPath;
    std::string actualSha256;
    std::uintmax_t actualSizeBytes = 0;
    std::vector<std::string> blockers;
    bool networkRequested = false;
    bool executableLoaded = false;
};

class Sha256 final {
public:
    Sha256() { reset(); }

    void update(const std::uint8_t* data, std::size_t length) {
        for (std::size_t index = 0; index < length; ++index) {
            m_buffer[m_bufferSize++] = data[index];
            if (m_bufferSize == 64) {
                transform();
                m_bitLength += 512;
                m_bufferSize = 0;
            }
        }
    }

    std::string finalize() {
        std::size_t index = m_bufferSize;
        m_buffer[index++] = 0x80;
        if (index > 56) {
            while (index < 64) m_buffer[index++] = 0;
            transform();
            index = 0;
        }
        while (index < 56) m_buffer[index++] = 0;
        m_bitLength += static_cast<std::uint64_t>(m_bufferSize) * 8ULL;
        for (int shift = 7; shift >= 0; --shift) {
            m_buffer[56 + (7 - shift)] = static_cast<std::uint8_t>(m_bitLength >> (shift * 8));
        }
        transform();

        std::ostringstream output;
        output << std::hex << std::setfill('0');
        for (std::uint32_t word : m_state) output << std::setw(8) << word;
        return output.str();
    }

private:
    static constexpr std::array<std::uint32_t, 64> K = {
        0x428a2f98U,0x71374491U,0xb5c0fbcfU,0xe9b5dba5U,0x3956c25bU,0x59f111f1U,0x923f82a4U,0xab1c5ed5U,
        0xd807aa98U,0x12835b01U,0x243185beU,0x550c7dc3U,0x72be5d74U,0x80deb1feU,0x9bdc06a7U,0xc19bf174U,
        0xe49b69c1U,0xefbe4786U,0x0fc19dc6U,0x240ca1ccU,0x2de92c6fU,0x4a7484aaU,0x5cb0a9dcU,0x76f988daU,
        0x983e5152U,0xa831c66dU,0xb00327c8U,0xbf597fc7U,0xc6e00bf3U,0xd5a79147U,0x06ca6351U,0x14292967U,
        0x27b70a85U,0x2e1b2138U,0x4d2c6dfcU,0x53380d13U,0x650a7354U,0x766a0abbU,0x81c2c92eU,0x92722c85U,
        0xa2bfe8a1U,0xa81a664bU,0xc24b8b70U,0xc76c51a3U,0xd192e819U,0xd6990624U,0xf40e3585U,0x106aa070U,
        0x19a4c116U,0x1e376c08U,0x2748774cU,0x34b0bcb5U,0x391c0cb3U,0x4ed8aa4aU,0x5b9cca4fU,0x682e6ff3U,
        0x748f82eeU,0x78a5636fU,0x84c87814U,0x8cc70208U,0x90befffaU,0xa4506cebU,0xbef9a3f7U,0xc67178f2U
    };

    static std::uint32_t rotateRight(std::uint32_t value, std::uint32_t bits) noexcept {
        return (value >> bits) | (value << (32U - bits));
    }

    void reset() noexcept {
        m_state = {0x6a09e667U,0xbb67ae85U,0x3c6ef372U,0xa54ff53aU,0x510e527fU,0x9b05688cU,0x1f83d9abU,0x5be0cd19U};
        m_buffer.fill(0);
        m_bufferSize = 0;
        m_bitLength = 0;
    }

    void transform() noexcept {
        std::array<std::uint32_t, 64> words{};
        for (std::size_t index = 0; index < 16; ++index) {
            const std::size_t offset = index * 4;
            words[index] = (static_cast<std::uint32_t>(m_buffer[offset]) << 24U)
                         | (static_cast<std::uint32_t>(m_buffer[offset + 1]) << 16U)
                         | (static_cast<std::uint32_t>(m_buffer[offset + 2]) << 8U)
                         | static_cast<std::uint32_t>(m_buffer[offset + 3]);
        }
        for (std::size_t index = 16; index < 64; ++index) {
            const std::uint32_t s0 = rotateRight(words[index - 15], 7U) ^ rotateRight(words[index - 15], 18U) ^ (words[index - 15] >> 3U);
            const std::uint32_t s1 = rotateRight(words[index - 2], 17U) ^ rotateRight(words[index - 2], 19U) ^ (words[index - 2] >> 10U);
            words[index] = words[index - 16] + s0 + words[index - 7] + s1;
        }

        std::uint32_t a = m_state[0], b = m_state[1], c = m_state[2], d = m_state[3];
        std::uint32_t e = m_state[4], f = m_state[5], g = m_state[6], h = m_state[7];
        for (std::size_t index = 0; index < 64; ++index) {
            const std::uint32_t s1 = rotateRight(e, 6U) ^ rotateRight(e, 11U) ^ rotateRight(e, 25U);
            const std::uint32_t choice = (e & f) ^ ((~e) & g);
            const std::uint32_t temp1 = h + s1 + choice + K[index] + words[index];
            const std::uint32_t s0 = rotateRight(a, 2U) ^ rotateRight(a, 13U) ^ rotateRight(a, 22U);
            const std::uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
            const std::uint32_t temp2 = s0 + majority;
            h = g; g = f; f = e; e = d + temp1; d = c; c = b; b = a; a = temp1 + temp2;
        }
        m_state[0] += a; m_state[1] += b; m_state[2] += c; m_state[3] += d;
        m_state[4] += e; m_state[5] += f; m_state[6] += g; m_state[7] += h;
    }

    std::array<std::uint32_t, 8> m_state{};
    std::array<std::uint8_t, 64> m_buffer{};
    std::size_t m_bufferSize = 0;
    std::uint64_t m_bitLength = 0;
};

class OnnxModelManager final {
public:
    explicit OnnxModelManager(std::filesystem::path modelRoot)
        : m_modelRoot(std::move(modelRoot)) {}

    static bool validSha256(const std::string& value) noexcept {
        if (value.size() != 64) return false;
        for (char character : value) {
            const bool digit = character >= '0' && character <= '9';
            const bool lower = character >= 'a' && character <= 'f';
            if (!digit && !lower) return false;
        }
        return true;
    }

    static std::string sha256File(const std::filesystem::path& path) {
        std::ifstream input(path, std::ios::binary);
        if (!input) return {};
        Sha256 hash;
        std::array<char, 64 * 1024> buffer{};
        while (input) {
            input.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
            const auto count = input.gcount();
            if (count > 0) hash.update(reinterpret_cast<const std::uint8_t*>(buffer.data()), static_cast<std::size_t>(count));
        }
        return input.bad() ? std::string{} : hash.finalize();
    }

    OnnxModelEvidence inspect(const OnnxModelDescriptor& descriptor) const {
        OnnxModelEvidence evidence;
        evidence.descriptor = descriptor;
        if (!descriptor.enabled) {
            evidence.status = ModelEvidenceStatus::DORMANT;
            evidence.blockers.push_back("MODEL_DISABLED");
            return evidence;
        }
        if (descriptor.id.empty()) evidence.blockers.push_back("MODEL_ID_MISSING");
        if (descriptor.purpose.empty()) evidence.blockers.push_back("MODEL_PURPOSE_MISSING");
        if (descriptor.relativePath.empty() || descriptor.relativePath.is_absolute()) evidence.blockers.push_back("MODEL_PATH_INVALID");
        if (descriptor.relativePath.extension() != ".onnx") evidence.blockers.push_back("MODEL_PATH_NOT_ONNX");
        if (!validSha256(descriptor.expectedSha256)) evidence.blockers.push_back("MODEL_SHA256_NOT_PINNED");
        if (descriptor.expectedSizeBytes == 0) evidence.blockers.push_back("MODEL_SIZE_NOT_PINNED");
        if (descriptor.minimumVramGiB < 0.0) evidence.blockers.push_back("MODEL_VRAM_INVALID");
        if (descriptor.qualityScore < 1 || descriptor.qualityScore > 100) evidence.blockers.push_back("MODEL_QUALITY_INVALID");
        if (descriptor.maxConcurrency < 1) evidence.blockers.push_back("MODEL_CONCURRENCY_INVALID");
        if (descriptor.providers.empty()) evidence.blockers.push_back("MODEL_PROVIDERS_MISSING");
        if (descriptor.precisions.empty()) evidence.blockers.push_back("MODEL_PRECISIONS_MISSING");
        if (!evidence.blockers.empty()) return evidence;

        std::error_code error;
        const auto root = std::filesystem::weakly_canonical(m_modelRoot, error);
        if (error || root.empty()) {
            evidence.blockers.push_back("MODEL_ROOT_UNAVAILABLE");
            return evidence;
        }
        const auto candidate = std::filesystem::weakly_canonical(root / descriptor.relativePath, error);
        if (error || candidate.empty()) {
            evidence.blockers.push_back("MODEL_FILE_MISSING");
            return evidence;
        }
        const auto relative = std::filesystem::relative(candidate, root, error);
        if (error || relative.empty() || *relative.begin() == "..") {
            evidence.blockers.push_back("MODEL_PATH_ESCAPES_ROOT");
            return evidence;
        }
        evidence.resolvedPath = candidate;
        const auto linkStatus = std::filesystem::symlink_status(candidate, error);
        if (error || std::filesystem::is_symlink(linkStatus)) {
            evidence.blockers.push_back("MODEL_SYMLINK_FORBIDDEN");
            return evidence;
        }
        if (!std::filesystem::is_regular_file(candidate, error) || error) {
            evidence.blockers.push_back("MODEL_NOT_REGULAR_FILE");
            return evidence;
        }
        evidence.actualSizeBytes = std::filesystem::file_size(candidate, error);
        if (error) {
            evidence.blockers.push_back("MODEL_SIZE_UNREADABLE");
            return evidence;
        }
        if (evidence.actualSizeBytes != descriptor.expectedSizeBytes) evidence.blockers.push_back("MODEL_SIZE_MISMATCH");
        evidence.actualSha256 = sha256File(candidate);
        if (evidence.actualSha256.empty()) evidence.blockers.push_back("MODEL_HASH_UNREADABLE");
        else if (evidence.actualSha256 != descriptor.expectedSha256) evidence.blockers.push_back("MODEL_SHA256_MISMATCH");
        evidence.status = evidence.blockers.empty() ? ModelEvidenceStatus::PASS : ModelEvidenceStatus::BLOCKED;
        return evidence;
    }

    std::vector<OnnxModelEvidence> inspectAll(const std::vector<OnnxModelDescriptor>& descriptors) const {
        std::vector<OnnxModelEvidence> result;
        result.reserve(descriptors.size());
        for (const auto& descriptor : descriptors) result.push_back(inspect(descriptor));
        return result;
    }

private:
    std::filesystem::path m_modelRoot;
};

} // namespace echoes::neural
