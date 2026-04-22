#pragma once
/**
 * EmotionalCore.h  —  EchoesEcosystem Emotional Architecture
 * ============================================================
 *
 * Implémentation complète du système émotionnel décrit dans le plan :
 *
 *   EmotionalProfile      → État émotionnel instantané
 *   EmotionalMemory       → Inertie masse-ressort, mémoire long-terme
 *   EmotionalFieldMatrix  → Source d'influence unique sur tous les paramètres
 *   MicroDriftEngine      → Variation organique lente (le moteur "respire")
 *   FatigueEngine         → Accumulation temporelle, fatigue cumulative
 *   EmotionalClock        → Timer global unique (cohérence totale)
 *
 * Usage:
 *   EmotionalCore core(44100.f);
 *   core.setState(EmotionalState::Tension, 0.8f);
 *
 *   // Dans le loop audio:
 *   core.tick(blockSize);
 *   auto field = core.getField();   // → tous les paramètres
 *   // field.spatialWidth, field.saturation, field.compRatio, etc.
 */

#include <cmath>
#include <algorithm>
#include <array>
#include <string>
#include <atomic>
#include <mutex>
#include <deque>

namespace echoes::emotional {

// ─────────────────────────────────────────────────────────────────────────────
// EMOTIONAL STATE
// ─────────────────────────────────────────────────────────────────────────────

enum class EmotionalState {
    Neutral    = 0,
    Tension    = 1,
    Release    = 2,
    Aggression = 3,
    Intimacy   = 4,
    Euphoria   = 5,
    Grief      = 6,
    Power      = 7,
    COUNT      = 8
};

inline const char* stateName(EmotionalState s) {
    switch (s) {
        case EmotionalState::Neutral:    return "Neutral";
        case EmotionalState::Tension:    return "Tension";
        case EmotionalState::Release:    return "Release";
        case EmotionalState::Aggression: return "Aggression";
        case EmotionalState::Intimacy:   return "Intimacy";
        case EmotionalState::Euphoria:   return "Euphoria";
        case EmotionalState::Grief:      return "Grief";
        case EmotionalState::Power:      return "Power";
        default:                         return "Unknown";
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// EMOTIONAL PROFILE — état instantané
// ─────────────────────────────────────────────────────────────────────────────

struct EmotionalProfile {
    EmotionalState state       = EmotionalState::Neutral;
    float          intensity   = 0.0f;   // 0..1  force de l'état
    float          tension     = 0.0f;   // 0..1  tension accumulée
    float          breathiness = 0.3f;   // 0..1  caractère du souffle
    float          fatigue     = 0.0f;   // 0..1  fatigue cumulative (temps)

    // Pondérations multi-états (top-2)
    struct StateWeight {
        EmotionalState state = EmotionalState::Neutral;
        float          weight = 0.0f;
    };
    std::array<StateWeight, 2> blendStates;

    bool isBlending() const noexcept {
        return blendStates[1].weight > 0.01f;
    }
};

// ─────────────────────────────────────────────────────────────────────────────
// EMOTIONAL FIELD MATRIX — source unique d'influence
// ─────────────────────────────────────────────────────────────────────────────

struct EmotionalField {
    // Spatial
    float spatialWidth       = 1.0f;    // 0.2..2.0  (1.0 = neutre)
    float spatialDepth       = 1.0f;    // 0.0..1.0  (depth de la réverbération)
    float decorrelation      = 0.3f;    // 0.0..1.0  (L/R décorrélation)
    float itdMs              = 0.3f;    // 0..1.5ms  (interaural time diff)

    // Dynamics
    float compRatio          = 4.0f;    // 1.5..10.0
    float compThreshDb       = -18.0f;  // -30..-6
    float gateThreshDb       = -40.0f;  // -60..-20
    float makeupGainDb       = 0.0f;    // -3..+6

    // Timbre / EQ
    float presenceGainDb     = 3.0f;    // -6..+9   (band 3kHz)
    float airGainDb          = 2.0f;    // -3..+6   (band 10kHz)
    float bodyGainDb         = -2.0f;   // -6..+3   (band 200Hz)
    float warmthHz           = 200.0f;  // 100..400

    // Saturation / Color
    float saturation         = 0.0f;    // 0..1
    float glue               = 0.5f;    // 0..1  (MixBus cohésion)
    float headroom           = 0.0f;    // dB headroom control

    // AI Voice
    float timbreDepth        = 0.0f;    // 0..1
    float breathPreservation = 0.8f;    // 0..1
    float pitchShift         = 0.0f;    // semitones

    // Micro drift (variation organique)
    float driftWidth         = 0.0f;    // amplitude drift spatial
    float driftDecorr        = 0.0f;    // amplitude drift décorr
    float driftPitch         = 0.0f;    // amplitude drift pitch (cents)

    // Fatigue
    float fatigueLevel       = 0.0f;    // 0..1 niveau de fatigue global
    float stabilityReduction = 0.0f;    // 0..0.5 instabilité due à fatigue
};

// ─────────────────────────────────────────────────────────────────────────────
// EMOTIONAL MEMORY — système masse-ressort
// ─────────────────────────────────────────────────────────────────────────────

class EmotionalMemory {
public:
    struct SpringConfig {
        float mass        = 1.0f;     // inertie
        float stiffness   = 2.5f;     // raideur (vitesse de réponse)
        float damping     = 0.85f;    // amortissement (overshoot)
        float maxVelocity = 2.0f;     // cap vitesse
    };

    EmotionalMemory() : m_sr(44100.f), m_cfg() {}
    explicit EmotionalMemory(float sampleRate, SpringConfig cfg)
        : m_sr(sampleRate), m_cfg(cfg) {}

    // Cible vers laquelle l'état converge
    void setTarget(float target) noexcept { m_target = target; }

    // Mise à jour par block (appelée dans tick())
    float update(int blockSize) noexcept {
        float dt = static_cast<float>(blockSize) / m_sr;
        float spring = -m_cfg.stiffness * (m_position - m_target);
        float damp   = -m_cfg.damping   *  m_velocity;
        float force  = (spring + damp) / m_cfg.mass;
        m_velocity   = std::clamp(m_velocity + force * dt, -m_cfg.maxVelocity, m_cfg.maxVelocity);
        m_position  += m_velocity * dt;
        return m_position;
    }

    float position() const noexcept { return m_position; }
    float velocity() const noexcept { return m_velocity; }

    // Long-term memory (10-20s intention narrative)
    void pushHistory(float val) {
        m_history.push_back(val);
        if (m_history.size() > kHistorySize) m_history.pop_front();
    }

    float longTermMean() const noexcept {
        if (m_history.empty()) return m_position;
        float sum = 0.f;
        for (float v : m_history) sum += v;
        return sum / m_history.size();
    }

    // Influence du passé sur le présent (narrative intent)
    float narrativeInfluence() const noexcept {
        float lt = longTermMean();
        float diff = m_position - lt;
        return std::tanh(diff * 2.0f);  // -1..+1, signature de la trajectoire
    }

private:
    static constexpr size_t kHistorySize = 1764; // ~20s @ 20Hz update
    float m_sr;
    SpringConfig m_cfg;
    float m_position = 0.f;
    float m_velocity = 0.f;
    float m_target   = 0.f;
    std::deque<float> m_history;
};

// ─────────────────────────────────────────────────────────────────────────────
// MICRO DRIFT ENGINE — variation organique lente
// ─────────────────────────────────────────────────────────────────────────────

class MicroDriftEngine {
public:
    struct Config {
        float widthDepth   = 0.08f;   // amplitude max du drift width
        float decorrDepth  = 0.05f;   // amplitude drift décorr
        float pitchDepth   = 3.0f;    // amplitude drift pitch (cents)
        float widthRateHz  = 0.07f;   // fréquence oscillation width
        float decorrRateHz = 0.13f;   // fréquence oscillation décorr
        float pitchRateHz  = 0.05f;   // fréquence oscillation pitch
    };

    static Config defaultConfig() { return Config{}; }
    explicit MicroDriftEngine(float sampleRate, Config cfg = MicroDriftEngine::defaultConfig())
        : m_sr(sampleRate), m_cfg(cfg) {}

    void tick(int blockSize) noexcept {
        float dt  = static_cast<float>(blockSize) / m_sr;
        m_phaseW += m_cfg.widthRateHz  * dt * 2.f * 3.14159f;
        m_phaseD += m_cfg.decorrRateHz * dt * 2.f * 3.14159f;
        m_phaseP += m_cfg.pitchRateHz  * dt * 2.f * 3.14159f;
    }

    // Valeurs de drift (appelées depuis EmotionalField après tick)
    float widthOffset()  const noexcept {
        return m_cfg.widthDepth  * std::sin(m_phaseW)
             + m_cfg.widthDepth  * 0.3f * std::sin(m_phaseW * 2.3f + 0.5f);
    }
    float decorrOffset() const noexcept {
        return m_cfg.decorrDepth * std::sin(m_phaseD)
             + m_cfg.decorrDepth * 0.2f * std::cos(m_phaseD * 1.7f + 1.2f);
    }
    float pitchOffset()  const noexcept {
        return m_cfg.pitchDepth  * std::sin(m_phaseP)
             + m_cfg.pitchDepth  * 0.4f * std::sin(m_phaseP * 3.1f);
    }

    void setIntensityScale(float s) noexcept { m_scale = std::clamp(s, 0.f, 2.f); }

private:
    float m_sr;
    Config m_cfg;
    float m_phaseW = 0.f;
    float m_phaseD = 1.3f;   // phases décalées → asymétrie naturelle
    float m_phaseP = 2.7f;
    float m_scale  = 1.0f;
};

// ─────────────────────────────────────────────────────────────────────────────
// FATIGUE ENGINE — accumulation temporelle
// ─────────────────────────────────────────────────────────────────────────────

class FatigueEngine {
public:
    struct Config {
        float buildRate   = 0.002f;  // vitesse accumulation (par seconde d'intensité)
        float decayRate   = 0.0005f; // vitesse récupération (par seconde de silence)
        float maxFatigue  = 1.0f;    // plafond
        float pitchLeakAt = 0.6f;    // seuil: fatigue > X → pitch micro-instabilité
        float breathLeakAt= 0.4f;    // seuil: fatigue > X → breath leakage
        float stabilityAt = 0.8f;    // seuil: fatigue > X → instabilité générale
    };

    FatigueEngine() : m_cfg() {}
    explicit FatigueEngine(Config cfg) : m_cfg(cfg) {}

    // Appelé chaque block avec l'intensité courante et le temps écoulé
    void update(float intensity, float dtSec) noexcept {
        if (intensity > 0.1f) {
            // Accumulation proportionnelle à l'intensité
            m_fatigue += m_cfg.buildRate * intensity * dtSec;
        } else {
            // Récupération
            m_fatigue -= m_cfg.decayRate * dtSec;
        }
        m_fatigue = std::clamp(m_fatigue, 0.f, m_cfg.maxFatigue);

        // Tension s'installe avec la fatigue
        m_tension = m_fatigue * 0.6f + intensity * 0.4f;
    }

    float fatigue()  const noexcept { return m_fatigue; }
    float tension()  const noexcept { return m_tension; }

    // Effets applicables au field
    float pitchInstability()  const noexcept {
        float excess = std::max(0.f, m_fatigue - m_cfg.pitchLeakAt);
        return excess / (1.f - m_cfg.pitchLeakAt + 1e-6f);
    }
    float breathLeakage() const noexcept {
        float excess = std::max(0.f, m_fatigue - m_cfg.breathLeakAt);
        return excess / (1.f - m_cfg.breathLeakAt + 1e-6f);
    }
    float stabilityReduction() const noexcept {
        float excess = std::max(0.f, m_fatigue - m_cfg.stabilityAt);
        return excess / (1.f - m_cfg.stabilityAt + 1e-6f);
    }

    void reset() noexcept { m_fatigue = 0.f; m_tension = 0.f; }

private:
    Config m_cfg;
    float  m_fatigue = 0.f;
    float  m_tension = 0.f;
};

// ─────────────────────────────────────────────────────────────────────────────
// EMOTIONAL CLOCK — timer global unique
// ─────────────────────────────────────────────────────────────────────────────

class EmotionalClock {
public:
    explicit EmotionalClock(float sampleRate) : m_sr(sampleRate) {}

    void tick(int blockSize) noexcept {
        float dt    = static_cast<float>(blockSize) / m_sr;
        m_timeSec  += dt;
        m_phase    += dt;
        m_beatPhase = std::fmod(m_beatPhase + dt / m_beatPeriodSec, 1.0f);
    }

    float timeSec()   const noexcept { return m_timeSec; }
    float phase()     const noexcept { return m_phase; }
    float beatPhase() const noexcept { return m_beatPhase; }

    void  setBPM(float bpm) noexcept {
        m_beatPeriodSec = 60.f / std::max(1.f, bpm);
    }

    // Pulsation lente pour les modulations globales
    float slowPulse(float periodSec = 4.0f) const noexcept {
        return 0.5f + 0.5f * std::sin(m_timeSec * 2.f * 3.14159f / periodSec);
    }

private:
    float m_sr;
    float m_timeSec      = 0.f;
    float m_phase        = 0.f;
    float m_beatPhase    = 0.f;
    float m_beatPeriodSec= 0.5f; // 120 BPM par défaut
};

// ─────────────────────────────────────────────────────────────────────────────
// EMOTIONAL CORE — orchestrateur principal
// ─────────────────────────────────────────────────────────────────────────────

class EmotionalCore {
public:
    explicit EmotionalCore(float sampleRate)
        : m_sr(sampleRate)
        , m_clock(sampleRate)
        , m_drift(sampleRate)
        , m_fatigue()
        , m_memIntensity(sampleRate, []{ EmotionalMemory::SpringConfig c; c.mass=1.0f; c.stiffness=2.5f; c.damping=0.85f; return c; }())
        , m_memTension(sampleRate,   []{ EmotionalMemory::SpringConfig c; c.mass=1.5f; c.stiffness=1.8f; c.damping=0.90f; return c; }())
        , m_memSpatial(sampleRate,   []{ EmotionalMemory::SpringConfig c; c.mass=0.8f; c.stiffness=3.0f; c.damping=0.80f; return c; }())
    {}

    // ── API principale ────────────────────────────────────────────────────────

    void setState(EmotionalState state, float intensity) noexcept {
        std::lock_guard lk(m_mu);
        m_profile.state     = state;
        m_profile.intensity = std::clamp(intensity, 0.f, 1.f);
        _updateTargets();
    }

    void setBlend(EmotionalState s1, float w1,
                  EmotionalState s2, float w2) noexcept {
        std::lock_guard lk(m_mu);
        float total = w1 + w2;
        m_profile.blendStates[0] = {s1, w1 / total};
        m_profile.blendStates[1] = {s2, w2 / total};
        _updateTargets();
    }

    void setIntensity(float i) noexcept {
        std::lock_guard lk(m_mu);
        m_profile.intensity = std::clamp(i, 0.f, 1.f);
        _updateTargets();
    }

    void setBreathiness(float b) noexcept {
        std::lock_guard lk(m_mu);
        m_profile.breathiness = std::clamp(b, 0.f, 1.f);
    }

    void setBPM(float bpm) noexcept { m_clock.setBPM(bpm); }

    // ── Tick — appelé AVANT process() dans la chaîne audio ───────────────────
    void tick(int blockSize) noexcept {
        m_clock.tick(blockSize);
        m_drift.tick(blockSize);

        float dt = static_cast<float>(blockSize) / m_sr;

        // Masse-ressort pour intensité, tension, spatial
        float newIntensity = m_memIntensity.update(blockSize);
        float newTension   = m_memTension.update(blockSize);
        float newSpatial   = m_memSpatial.update(blockSize);

        // Mise à jour long-term memory à 20Hz (~toutes les 2048 samples)
        m_histCounter += blockSize;
        if (m_histCounter >= 2048) {
            m_histCounter = 0;
            m_memIntensity.pushHistory(newIntensity);
            m_memTension.pushHistory(newTension);
        }

        // Fatigue cumulative
        m_fatigue.update(m_profile.intensity, dt);

        // Recalcul du field
        _computeField(newIntensity, newTension, newSpatial);
    }

    // ── Accesseurs ────────────────────────────────────────────────────────────
    EmotionalField   getField()   const noexcept { return m_field; }
    EmotionalProfile getProfile() const noexcept { return m_profile; }
    float            timeSec()    const noexcept { return m_clock.timeSec(); }
    float            fatigue()    const noexcept { return m_fatigue.fatigue(); }

    // Narrative influence: signature de trajectoire (-1..+1)
    float narrativeInfluence() const noexcept {
        return m_memIntensity.narrativeInfluence();
    }

private:
    // ── Table des archétypes émotionnels ─────────────────────────────────────
    // Chaque état définit un "vecteur de base" dans EmotionalField
    struct StateArchetype {
        float spatialWidth;    float spatialDepth;  float decorrelation;
        float compRatio;       float compThreshDb;  float gateThreshDb;
        float presenceDb;      float airDb;         float bodyDb;
        float saturation;      float glue;          float timbreDepth;
        float breathPreserve;
    };

    static constexpr std::array<StateArchetype, 8> kArchetypes = {{
        // Neutral
        {1.0f, 0.3f, 0.25f,  4.0f,-18.f,-40.f,  3.0f, 2.0f,-2.0f,  0.00f,0.40f, 0.0f, 0.80f},
        // Tension
        {0.7f, 0.6f, 0.15f,  6.0f,-14.f,-36.f,  5.0f, 1.0f,-3.5f,  0.15f,0.55f, 0.2f, 0.65f},
        // Release
        {1.4f, 0.5f, 0.40f,  2.5f,-22.f,-45.f,  1.5f, 4.0f,-1.0f,  0.05f,0.30f, 0.1f, 0.90f},
        // Aggression
        {0.6f, 0.7f, 0.10f,  8.0f,-12.f,-30.f,  7.0f, 0.5f,-4.0f,  0.35f,0.70f, 0.4f, 0.50f},
        // Intimacy
        {0.5f, 0.2f, 0.10f,  3.0f,-24.f,-48.f,  1.0f, 3.5f,-1.5f,  0.00f,0.20f, 0.0f, 0.95f},
        // Euphoria
        {1.8f, 0.8f, 0.55f,  3.5f,-20.f,-42.f,  4.0f, 5.0f,-0.5f,  0.10f,0.50f, 0.3f, 0.75f},
        // Grief
        {0.8f, 0.9f, 0.45f,  2.0f,-26.f,-50.f, -1.0f, 1.5f,-0.5f,  0.05f,0.25f, 0.1f, 0.92f},
        // Power
        {1.1f, 0.5f, 0.20f,  7.0f,-10.f,-28.f,  6.0f, 1.5f,-3.0f,  0.40f,0.80f, 0.5f, 0.60f},
    }};

    // Interpolation linéaire entre deux archétypes
    static StateArchetype lerp(const StateArchetype& a, const StateArchetype& b, float t) {
        auto mix = [t](float x, float y) { return x + t*(y-x); };
        return {
            mix(a.spatialWidth,  b.spatialWidth),
            mix(a.spatialDepth,  b.spatialDepth),
            mix(a.decorrelation, b.decorrelation),
            mix(a.compRatio,     b.compRatio),
            mix(a.compThreshDb,  b.compThreshDb),
            mix(a.gateThreshDb,  b.gateThreshDb),
            mix(a.presenceDb,    b.presenceDb),
            mix(a.airDb,         b.airDb),
            mix(a.bodyDb,        b.bodyDb),
            mix(a.saturation,    b.saturation),
            mix(a.glue,          b.glue),
            mix(a.timbreDepth,   b.timbreDepth),
            mix(a.breathPreserve,b.breathPreserve),
        };
    }

    void _updateTargets() {
        // Cible masse-ressort basée sur le profil
        m_memIntensity.setTarget(m_profile.intensity);
        m_memTension.setTarget(m_profile.tension);
        float spatialTarget = _stateToSpatialWidth(m_profile.state, m_profile.intensity);
        m_memSpatial.setTarget(spatialTarget);
    }

    float _stateToSpatialWidth(EmotionalState s, float intensity) {
        int idx = static_cast<int>(s);
        return kArchetypes[idx].spatialWidth * (0.7f + 0.3f * intensity);
    }

    void _computeField(float intensity, float tension, float spatial) {
        // 1. Archétype de base (ou blend de 2)
        StateArchetype arch;
        if (m_profile.isBlending()) {
            int i0 = static_cast<int>(m_profile.blendStates[0].state);
            int i1 = static_cast<int>(m_profile.blendStates[1].state);
            float t = m_profile.blendStates[1].weight;
            arch = lerp(kArchetypes[i0], kArchetypes[i1], t);
        } else {
            arch = kArchetypes[static_cast<int>(m_profile.state)];
        }

        // 2. Moduler par intensité (masse-ressort, pas brute)
        float I = intensity;
        float T = tension;

        m_field.spatialWidth    = arch.spatialWidth  * (0.7f + 0.3f * spatial);
        m_field.spatialDepth    = arch.spatialDepth  * (0.5f + 0.5f * I);
        m_field.decorrelation   = arch.decorrelation * (0.6f + 0.4f * I);
        m_field.itdMs           = 0.1f + 0.8f * arch.spatialWidth * I;

        m_field.compRatio       = 1.5f + (arch.compRatio - 1.5f) * I;
        m_field.compThreshDb    = arch.compThreshDb + T * 4.f;  // + de tension → + de compression
        m_field.gateThreshDb    = arch.gateThreshDb + T * 8.f;
        m_field.makeupGainDb    = I * 2.f;

        m_field.presenceGainDb  = arch.presenceDb * (0.5f + 0.5f * I);
        m_field.airGainDb       = arch.airDb * I;
        m_field.bodyGainDb      = arch.bodyDb;

        m_field.saturation      = arch.saturation * I;
        m_field.glue            = arch.glue;
        m_field.headroom        = -I * 1.5f;  // plus intense → moins de headroom

        m_field.timbreDepth     = arch.timbreDepth * I;
        m_field.breathPreservation = arch.breathPreserve
                                   + m_profile.breathiness * 0.1f;

        // 3. Micro drift (le moteur respire)
        m_field.driftWidth  = m_drift.widthOffset()  * (0.5f + 0.5f * I);
        m_field.driftDecorr = m_drift.decorrOffset() * (0.5f + 0.5f * I);
        m_field.driftPitch  = m_drift.pitchOffset()  * (0.5f + 0.5f * I);

        // 4. Fatigue cumulative
        float fat = m_fatigue.fatigue();
        m_field.fatigueLevel       = fat;
        m_field.stabilityReduction = m_fatigue.stabilityReduction();
        // Instabilité de pitch due à la fatigue
        m_field.driftPitch += m_fatigue.pitchInstability() * 8.f;
        // Breath leakage → preservation diminue sous fatigue
        m_field.breathPreservation -= m_fatigue.breathLeakage() * 0.3f;
        m_field.breathPreservation  = std::clamp(m_field.breathPreservation, 0.f, 1.f);

        // 5. Influence narrative (intention long-terme)
        float narr = narrativeInfluence();
        m_field.saturation += narr * 0.05f;   // légère teinte du passé

        // 6. Clock pulse (pulsation lente)
        float pulse = m_clock.slowPulse(8.0f);
        m_field.spatialWidth += m_field.driftWidth + pulse * 0.03f;
        m_field.decorrelation = std::clamp(
            m_field.decorrelation + m_field.driftDecorr, 0.f, 0.95f);
    }

    // ── State ─────────────────────────────────────────────────────────────────
    float           m_sr;
    EmotionalClock  m_clock;
    MicroDriftEngine m_drift;
    FatigueEngine   m_fatigue;
    EmotionalMemory m_memIntensity;
    EmotionalMemory m_memTension;
    EmotionalMemory m_memSpatial;

    EmotionalProfile m_profile;
    EmotionalField   m_field;

    std::mutex m_mu;
    int        m_histCounter = 0;
};

} // namespace echoes::emotional
