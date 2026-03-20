#include "EchoesEngine/Core/module.h"

#include <utility>

namespace echoes::core {

Module::Module(std::string name)
    : name_(std::move(name)) {}

Module::~Module() = default;

void Module::onRegister(ModuleContext& context) {
    context_ = &context;
}

void Module::start() {}

void Module::update(double) {}

void Module::stop() {}

std::string_view Module::name() const noexcept {
    return name_;
}

ModuleContext* Module::context() const noexcept {
    return context_;
}

} // namespace echoes::core
