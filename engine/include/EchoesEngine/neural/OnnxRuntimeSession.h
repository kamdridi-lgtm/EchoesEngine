#pragma once

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <limits>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#ifdef ECHOES_HAS_ONNXRUNTIME
#include <onnxruntime_cxx_api.h>
#endif

namespace echoes::neural {

struct OnnxRuntimeInferenceResult {
    std::string schema = "echoes.onnx-runtime-inference-result.v1";
    std::string status = "BLOCKED";
    std::string provider = "CPUExecutionProvider";
    std::vector<float> output;
    std::vector<std::int64_t> outputShape;
    double inferenceMs = 0.0;
    std::vector<std::string> blockers;
    bool modelLoaded = false;
    bool inferenceExecuted = false;
    bool outputFinite = false;
    bool gpuAccelerated = false;
    bool tensorRtUsed = false;
};

class OnnxRuntimeSession final {
public:
    OnnxRuntimeSession()
#ifdef ECHOES_HAS_ONNXRUNTIME
        : m_env(ORT_LOGGING_LEVEL_WARNING, "EchoesOnnxRuntime")
#endif
    {
#ifdef ECHOES_HAS_ONNXRUNTIME
        m_options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_EXTENDED);
        m_options.SetIntraOpNumThreads(1);
        m_options.SetInterOpNumThreads(1);
#endif
    }

    bool load(const std::filesystem::path& modelPath) {
        reset();
        if (modelPath.empty() || modelPath.extension() != ".onnx") {
            m_loadBlockers.push_back("MODEL_PATH_NOT_ONNX");
            return false;
        }
        std::error_code error;
        if (!std::filesystem::is_regular_file(modelPath, error) || error) {
            m_loadBlockers.push_back("MODEL_FILE_UNAVAILABLE");
            return false;
        }
#ifndef ECHOES_HAS_ONNXRUNTIME
        m_loadBlockers.push_back("ONNXRUNTIME_NOT_COMPILED");
        return false;
#else
        try {
            m_session = std::make_unique<Ort::Session>(m_env, modelPath.c_str(), m_options);
            if (m_session->GetInputCount() != 1U) {
                m_loadBlockers.push_back("MODEL_INPUT_COUNT_UNSUPPORTED");
                resetSessionOnly();
                return false;
            }
            if (m_session->GetOutputCount() != 1U) {
                m_loadBlockers.push_back("MODEL_OUTPUT_COUNT_UNSUPPORTED");
                resetSessionOnly();
                return false;
            }

            Ort::AllocatorWithDefaultOptions allocator;
            auto inputName = m_session->GetInputNameAllocated(0, allocator);
            auto outputName = m_session->GetOutputNameAllocated(0, allocator);
            if (!inputName || !outputName) {
                m_loadBlockers.push_back("MODEL_TENSOR_NAMES_UNAVAILABLE");
                resetSessionOnly();
                return false;
            }
            m_inputName = inputName.get();
            m_outputName = outputName.get();

            const auto inputTypeInfo = m_session->GetInputTypeInfo(0);
            const auto outputTypeInfo = m_session->GetOutputTypeInfo(0);
            const auto inputTensorInfo = inputTypeInfo.GetTensorTypeAndShapeInfo();
            const auto outputTensorInfo = outputTypeInfo.GetTensorTypeAndShapeInfo();
            if (inputTensorInfo.GetElementType() != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT) {
                m_loadBlockers.push_back("MODEL_INPUT_TYPE_NOT_FLOAT");
            }
            if (outputTensorInfo.GetElementType() != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT) {
                m_loadBlockers.push_back("MODEL_OUTPUT_TYPE_NOT_FLOAT");
            }
            m_declaredInputShape = inputTensorInfo.GetShape();
            m_declaredOutputShape = outputTensorInfo.GetShape();
            if (m_declaredInputShape.empty()) m_loadBlockers.push_back("MODEL_INPUT_SHAPE_EMPTY");
            if (m_declaredOutputShape.empty()) m_loadBlockers.push_back("MODEL_OUTPUT_SHAPE_EMPTY");
            if (!m_loadBlockers.empty()) {
                resetSessionOnly();
                return false;
            }
            m_modelPath = std::filesystem::weakly_canonical(modelPath, error);
            if (error) m_modelPath = modelPath;
            m_loaded = true;
            return true;
        } catch (const Ort::Exception& exception) {
            m_loadBlockers.push_back(std::string("ONNXRUNTIME_LOAD_FAILED:") + exception.what());
            resetSessionOnly();
            return false;
        } catch (const std::exception& exception) {
            m_loadBlockers.push_back(std::string("MODEL_LOAD_FAILED:") + exception.what());
            resetSessionOnly();
            return false;
        }
#endif
    }

    OnnxRuntimeInferenceResult run(const std::vector<float>& input,
                                   const std::vector<std::int64_t>& shape) {
        OnnxRuntimeInferenceResult result;
        result.modelLoaded = m_loaded;
        if (!m_loaded) {
            result.blockers = m_loadBlockers;
            if (result.blockers.empty()) result.blockers.push_back("MODEL_NOT_LOADED");
            return result;
        }
        if (input.empty() || shape.empty()) {
            result.blockers.push_back("INPUT_EMPTY");
            return result;
        }
        std::size_t expectedElements = 1;
        for (const auto dimension : shape) {
            if (dimension <= 0) {
                result.blockers.push_back("INPUT_SHAPE_INVALID");
                return result;
            }
            const auto unsignedDimension = static_cast<std::size_t>(dimension);
            if (expectedElements > std::numeric_limits<std::size_t>::max() / unsignedDimension) {
                result.blockers.push_back("INPUT_SHAPE_OVERFLOW");
                return result;
            }
            expectedElements *= unsignedDimension;
        }
        if (expectedElements != input.size()) {
            result.blockers.push_back("INPUT_ELEMENT_COUNT_MISMATCH");
            return result;
        }
        if (!shapeCompatible(m_declaredInputShape, shape)) {
            result.blockers.push_back("INPUT_SHAPE_NOT_COMPATIBLE");
            return result;
        }
#ifndef ECHOES_HAS_ONNXRUNTIME
        result.blockers.push_back("ONNXRUNTIME_NOT_COMPILED");
        return result;
#else
        try {
            const auto started = std::chrono::steady_clock::now();
            auto memoryInfo = Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
            auto tensor = Ort::Value::CreateTensor<float>(
                memoryInfo,
                const_cast<float*>(input.data()),
                input.size(),
                shape.data(),
                shape.size());
            const char* inputNames[] = {m_inputName.c_str()};
            const char* outputNames[] = {m_outputName.c_str()};
            auto outputs = m_session->Run(
                Ort::RunOptions{nullptr},
                inputNames,
                &tensor,
                1,
                outputNames,
                1);
            result.inferenceMs = std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - started).count();
            result.inferenceExecuted = true;
            if (outputs.size() != 1U || !outputs.front().IsTensor()) {
                result.blockers.push_back("OUTPUT_TENSOR_MISSING");
                return result;
            }
            const auto info = outputs.front().GetTensorTypeAndShapeInfo();
            if (info.GetElementType() != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT) {
                result.blockers.push_back("OUTPUT_TYPE_NOT_FLOAT");
                return result;
            }
            result.outputShape = info.GetShape();
            const auto count = info.GetElementCount();
            const float* data = outputs.front().GetTensorData<float>();
            result.output.assign(data, data + count);
            result.outputFinite = std::all_of(
                result.output.begin(),
                result.output.end(),
                [](float value) { return std::isfinite(value); });
            if (!result.outputFinite) {
                result.blockers.push_back("OUTPUT_NON_FINITE");
                return result;
            }
            if (result.output.empty()) {
                result.blockers.push_back("OUTPUT_EMPTY");
                return result;
            }
            result.status = "PASS";
            return result;
        } catch (const Ort::Exception& exception) {
            result.blockers.push_back(std::string("ONNXRUNTIME_INFERENCE_FAILED:") + exception.what());
            return result;
        } catch (const std::exception& exception) {
            result.blockers.push_back(std::string("INFERENCE_FAILED:") + exception.what());
            return result;
        }
#endif
    }

    void reset() noexcept {
        resetSessionOnly();
        m_modelPath.clear();
        m_inputName.clear();
        m_outputName.clear();
        m_declaredInputShape.clear();
        m_declaredOutputShape.clear();
        m_loadBlockers.clear();
        m_loaded = false;
    }

    bool isLoaded() const noexcept { return m_loaded; }
    const std::filesystem::path& modelPath() const noexcept { return m_modelPath; }
    const std::vector<std::string>& loadBlockers() const noexcept { return m_loadBlockers; }
    const std::vector<std::int64_t>& declaredInputShape() const noexcept { return m_declaredInputShape; }
    const std::vector<std::int64_t>& declaredOutputShape() const noexcept { return m_declaredOutputShape; }

private:
    static bool shapeCompatible(const std::vector<std::int64_t>& declared,
                                const std::vector<std::int64_t>& actual) noexcept {
        if (declared.size() != actual.size()) return false;
        for (std::size_t index = 0; index < declared.size(); ++index) {
            if (declared[index] > 0 && declared[index] != actual[index]) return false;
        }
        return true;
    }

    void resetSessionOnly() noexcept {
#ifdef ECHOES_HAS_ONNXRUNTIME
        m_session.reset();
#endif
        m_loaded = false;
    }

#ifdef ECHOES_HAS_ONNXRUNTIME
    Ort::Env m_env;
    Ort::SessionOptions m_options;
    std::unique_ptr<Ort::Session> m_session;
#endif
    bool m_loaded = false;
    std::filesystem::path m_modelPath;
    std::string m_inputName;
    std::string m_outputName;
    std::vector<std::int64_t> m_declaredInputShape;
    std::vector<std::int64_t> m_declaredOutputShape;
    std::vector<std::string> m_loadBlockers;
};

} // namespace echoes::neural
