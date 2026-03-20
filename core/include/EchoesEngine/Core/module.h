#pragma once

#include "EchoesEngine/Core/context.h"

#include <string>
#include <string_view>

namespace echoes::core {

class Module {
public:
    explicit Module(std::string name);
    virtual ~Module();

    Module(const Module&) = delete;
    Module& operator=(const Module&) = delete;

    virtual void onRegister(ModuleContext& context);
    virtual void start();
    virtual void update(double deltaSeconds);
    virtual void stop();

    std::string_view name() const noexcept;
    ModuleContext* context() const noexcept;

protected:
    ModuleContext* context_ = nullptr;

private:
    std::string name_;
};

} // namespace echoes::core
