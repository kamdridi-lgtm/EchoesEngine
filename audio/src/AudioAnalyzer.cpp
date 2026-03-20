#include "EchoesEngine/audio/AudioAnalyzer.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <complex>
#include <deque>
#include <numeric>
#include <stdexcept>
#include <utility>
#include <vector>

namespace echoes::audio {

namespace {

constexpr float twoPi = 6.2831854820251465f;

static std::size_t log2Pow2(std::size_t value) {
    std::size_t result = 0;
    while ((std::size_t(1) << result) < value) {
        ++result;
    }
    return result;
}

class FFT {
public:
    explicit FFT(std::size_t size)
        : size_(size)
        , level_(log2Pow2(size))
        , buffer_(size)
        , reordered_(size)
    {
        if ((std::size_t(1) << level_) != size_) {
            throw std::invalid_argument("FFT size must be a power of two");
        }
        bitReverse_.resize(size_);
        for (std::size_t i = 0; i < size_; ++i) {
            std::size_t x = i;
            std::size_t reversed = 0;
            for (std::size_t bit = 0; bit < level_; ++bit) {
                reversed = (reversed << 1) | (x & 1);
                x >>= 1;
            }
            bitReverse_[i] = reversed;
        }
    }

    void forward(std::span<const float> input, std::span<std::complex<float>> output) {
        if (input.size() != size_ || output.size() < size_) {
            throw std::invalid_argument("FFT spans must match configured size");
        }

        for (std::size_t i = 0; i < size_; ++i) {
            buffer_[i] = std::complex<float>(input[i], 0.0f);
        }

        for (std::size_t i = 0; i < size_; ++i) {
            reordered_[i] = buffer_[bitReverse_[i]];
        }

        for (std::size_t span = 2; span <= size_; span <<= 1) {
            std::size_t half = span >> 1;
            float angle = -twoPi / static_cast<float>(span);
            std::complex<float> incremental(std::cos(angle), std::sin(angle));

            for (std::size_t block = 0; block < size_; block += span) {
                std::complex<float> twiddle(1.0f, 0.0f);
                for (std::size_t offset = 0; offset < half; ++offset) {
                    auto even = reordered_[block + offset];
                    auto odd = reordered_[block + offset + half] * twiddle;
                    reordered_[block + offset] = even + odd;
                    reordered_[block + offset + half] = even - odd;
                    twiddle *= incremental;
                }
            }
        }

        std::copy(reordered_.begin(), reordered_.end(), output.begin());
    }

private:
    const std::size_t size_;
    const std::size_t level_;
    std::vector<std::complex<float>> buffer_;
    std::vector<std::complex<float>> reordered_;
    std::vector<std::size_t> bitReverse_;
};

class BandAnalyzer {
public:
    explicit BandAnalyzer(std::size_t fftSize)
        : fftSize_(fftSize)
    {}

    void compute(const std::span<std::complex<float>>& spectrum, double sampleRate, float& bass, float& mid, float& treble) {
        const double binWidth = sampleRate / static_cast<double>(fftSize_);
        bass = mid = treble = 0.0f;
        auto energyAt = [&](double freqMin, double freqMax) {
            double sum = 0.0;
            for (std::size_t bin = 1; bin < fftSize_ / 2; ++bin) {
                double freq = bin * binWidth;
                if (freq < freqMin || freq >= freqMax) {
                    continue;
                }
                float magnitude = std::abs(spectrum[bin]);
                sum += magnitude * magnitude;
            }
            return static_cast<float>(sum);
        };

        bass = energyAt(20.0, 250.0);
        mid = energyAt(250.0, 4000.0);
        treble = energyAt(4000.0, 20000.0);

        const float normalize = std::max(1.0f, bass + mid + treble);
        bass /= normalize;
        mid /= normalize;
        treble /= normalize;
    }

private:
    const std::size_t fftSize_;
};

class BeatDetector {
public:
    explicit BeatDetector(const AnalyzerConfig& config)
        : config_(config)
    {}

    bool update(float energy, double timeStamp) {
        energyHistory_.push_back(energy);
        if (energyHistory_.size() > config_.energyHistory) {
            energyHistory_.pop_front();
        }

        const float average = std::accumulate(energyHistory_.begin(), energyHistory_.end(), 0.0f) /
                              std::max(static_cast<std::size_t>(1), energyHistory_.size());

        const bool energySpike = energy > average * config_.beatSensitivity;
        const bool onCooldown = (timeStamp - lastBeatTime_) < config_.beatCooldownSecs;

        if (energySpike && !onCooldown && energyHistory_.size() > 4) {
            lastBeatTime_ = timeStamp;
            confidence_ = std::min(1.0f, confidence_ + 0.25f);
            return true;
        }

        confidence_ = std::max(0.0f, confidence_ - 0.04f);
        return false;
    }

    float confidence() const noexcept {
        return confidence_;
    }

private:
    AnalyzerConfig config_;
    std::deque<float> energyHistory_;
    double lastBeatTime_ = -1.0;
    float confidence_ = 0.0f;
};

class TempoEstimator {
public:
    explicit TempoEstimator(const AnalyzerConfig& config)
        : config_(config)
    {}

    void recordBeat(double timeStamp) {
        if (lastBeatTime_ > 0.0) {
            intervals_.push_back(timeStamp - lastBeatTime_);
            if (intervals_.size() > 8) {
                intervals_.pop_front();
            }
        }
        lastBeatTime_ = timeStamp;
    }

    float tempo() const noexcept {
        if (intervals_.empty()) {
            return 0.0f;
        }
        const double averageInterval =
            std::accumulate(intervals_.begin(), intervals_.end(), 0.0) / static_cast<double>(intervals_.size());
        if (averageInterval <= 0.0) {
            return 0.0f;
        }
        const float measuredBpm = static_cast<float>(60.0 / averageInterval);
        const float smoothed = lastTempo_ + (measuredBpm - lastTempo_) * config_.tempoSmoothing;
        return smoothed;
    }

    void updateLastTempo(float tempo) {
        lastTempo_ = tempo;
    }

private:
    AnalyzerConfig config_;
    std::deque<double> intervals_;
    double lastBeatTime_ = -1.0;
    float lastTempo_ = 0.0f;
};

} // namespace

struct AudioAnalyzer::Impl {
    explicit Impl(AnalyzerConfig config)
        : config_(std::move(config))
        , fft_(config_.fftSize)
        , spectrum_(config_.fftSize)
        , window_(config_.fftSize)
        , tempBuffer_(config_.fftSize)
        , band_(config_.fftSize)
        , beatDetector_(config_)
        , tempoEstimator_(config_)
    {
        for (std::size_t i = 0; i < config_.fftSize; ++i) {
            window_[i] = 0.5f * (1.0f - std::cos(twoPi * static_cast<float>(i) / static_cast<float>(config_.fftSize)));
        }
    }

    void reset() {
        std::fill(spectrum_.begin(), spectrum_.end(), std::complex<float>(0.0f, 0.0f));
        std::fill(tempBuffer_.begin(), tempBuffer_.end(), 0.0f);
        features_ = RealtimeFeatures{};
        totalSeconds_ = 0.0;
        beatDetector_ = BeatDetector(config_);
        tempoEstimator_ = TempoEstimator(config_);
    }

    void process(std::span<const float> block, double sampleRate) {
        if (sampleRate <= 0.0 || block.empty()) {
            return;
        }

        std::fill(tempBuffer_.begin(), tempBuffer_.end(), 0.0f);
        const std::size_t copyCount = std::min(block.size(), tempBuffer_.size());
        std::copy_n(block.begin(), copyCount, tempBuffer_.begin());
        for (std::size_t i = 0; i < tempBuffer_.size(); ++i) {
            tempBuffer_[i] *= window_[i];
        }

        fft_.forward(tempBuffer_, spectrum_);

        float bass = 0.0f;
        float mid = 0.0f;
        float treble = 0.0f;
        band_.compute(spectrum_, sampleRate, bass, mid, treble);

        const float energy = computeEnergy(block.data(), copyCount);

        const bool beat = beatDetector_.update(energy, totalSeconds_);
        if (beat) {
            tempoEstimator_.recordBeat(totalSeconds_);
        }

        features_.bass = bass;
        features_.mid = mid;
        features_.treble = treble;
        features_.energy = energy;
        features_.beat = beat;
        features_.beatConfidence = beatDetector_.confidence();
        const float tempo = tempoEstimator_.tempo();
        if (tempo > 0.0f) {
            tempoEstimator_.updateLastTempo(tempo);
        }
        features_.tempo = tempo;

        totalSeconds_ += static_cast<double>(block.size()) / sampleRate;
    }

    static float computeEnergy(const float* buffer, std::size_t count) {
        float sum = 0.0f;
        for (std::size_t i = 0; i < count; ++i) {
            sum += buffer[i] * buffer[i];
        }
        if (count == 0) {
            return 0.0f;
        }
        return std::sqrt(sum / static_cast<float>(count));
    }

    AnalyzerConfig config_;
    FFT fft_;
    std::vector<std::complex<float>> spectrum_;
    std::vector<float> window_;
    std::vector<float> tempBuffer_;
    BandAnalyzer band_;
    BeatDetector beatDetector_;
    TempoEstimator tempoEstimator_;
    RealtimeFeatures features_;
    double totalSeconds_ = 0.0;
};

AudioAnalyzer::AudioAnalyzer(AnalyzerConfig config)
    : impl_(new Impl(std::move(config)))
{}

AudioAnalyzer::~AudioAnalyzer() {
    delete impl_;
}

void AudioAnalyzer::reset() noexcept {
    impl_->reset();
}

void AudioAnalyzer::processBlock(std::span<const float> block, double sampleRate) {
    impl_->process(block, sampleRate);
}

RealtimeFeatures AudioAnalyzer::features() const noexcept {
    return impl_->features_;
}

} // namespace echoes::audio
