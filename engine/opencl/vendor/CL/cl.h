#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define CL_API_ENTRY
#define CL_API_CALL
#define CL_CALLBACK

#define CL_VERSION_1_0 100
#define CL_VERSION_1_1 110
#define CL_VERSION_1_2 120
#define CL_VERSION_2_0 200
#define CL_VERSION_2_1 210
#define CL_VERSION_2_2 220
#define CL_VERSION_3_0 300

typedef int8_t cl_char;
typedef uint8_t cl_uchar;
typedef int16_t cl_short;
typedef uint16_t cl_ushort;
typedef int32_t cl_int;
typedef uint32_t cl_uint;
typedef int64_t cl_long;
typedef uint64_t cl_ulong;
typedef uint32_t cl_bool;
typedef uint64_t cl_bitfield;
typedef cl_bitfield cl_device_type;
typedef cl_bitfield cl_command_queue_properties;
typedef cl_bitfield cl_mem_flags;
typedef intptr_t cl_context_properties;
typedef cl_uint cl_platform_info;
typedef cl_uint cl_device_info;
typedef cl_uint cl_program_build_info;

typedef struct _cl_platform_id* cl_platform_id;
typedef struct _cl_device_id* cl_device_id;
typedef struct _cl_context* cl_context;
typedef struct _cl_command_queue* cl_command_queue;
typedef struct _cl_mem* cl_mem;
typedef struct _cl_program* cl_program;
typedef struct _cl_kernel* cl_kernel;
typedef struct _cl_event* cl_event;

#define CL_FALSE 0
#define CL_TRUE 1

#define CL_SUCCESS 0
#define CL_DEVICE_NOT_FOUND -1
#define CL_DEVICE_NOT_AVAILABLE -2
#define CL_COMPILER_NOT_AVAILABLE -3
#define CL_MEM_OBJECT_ALLOCATION_FAILURE -4
#define CL_OUT_OF_RESOURCES -5
#define CL_OUT_OF_HOST_MEMORY -6
#define CL_BUILD_PROGRAM_FAILURE -11
#define CL_INVALID_VALUE -30
#define CL_INVALID_DEVICE -33
#define CL_INVALID_BINARY -42
#define CL_INVALID_BUILD_OPTIONS -43
#define CL_INVALID_OPERATION -59

#define CL_PLATFORM_NAME 0x0902
#define CL_PLATFORM_VENDOR 0x0903

#define CL_DEVICE_TYPE_DEFAULT (1 << 0)
#define CL_DEVICE_TYPE_CPU (1 << 1)
#define CL_DEVICE_TYPE_GPU (1 << 2)
#define CL_DEVICE_TYPE_ACCELERATOR (1 << 3)
#define CL_DEVICE_TYPE_ALL 0xFFFFFFFF

#define CL_DEVICE_TYPE 0x1000
#define CL_DEVICE_VENDOR 0x102C
#define CL_DEVICE_NAME 0x102B
#define CL_DEVICE_MAX_COMPUTE_UNITS 0x1002
#define CL_DEVICE_GLOBAL_MEM_SIZE 0x101F

#define CL_CONTEXT_PLATFORM 0x1084

#define CL_MEM_READ_ONLY (1 << 2)
#define CL_MEM_WRITE_ONLY (1 << 1)
#define CL_MEM_COPY_HOST_PTR (1 << 5)

#define CL_PROGRAM_BUILD_LOG 0x1183

CL_API_ENTRY cl_int CL_API_CALL clGetPlatformIDs(
    cl_uint num_entries,
    cl_platform_id* platforms,
    cl_uint* num_platforms);

CL_API_ENTRY cl_int CL_API_CALL clGetPlatformInfo(
    cl_platform_id platform,
    cl_platform_info param_name,
    size_t param_value_size,
    void* param_value,
    size_t* param_value_size_ret);

CL_API_ENTRY cl_int CL_API_CALL clGetDeviceIDs(
    cl_platform_id platform,
    cl_device_type device_type,
    cl_uint num_entries,
    cl_device_id* devices,
    cl_uint* num_devices);

CL_API_ENTRY cl_int CL_API_CALL clGetDeviceInfo(
    cl_device_id device,
    cl_device_info param_name,
    size_t param_value_size,
    void* param_value,
    size_t* param_value_size_ret);

CL_API_ENTRY cl_context CL_API_CALL clCreateContext(
    const cl_context_properties* properties,
    cl_uint num_devices,
    const cl_device_id* devices,
    void (CL_CALLBACK* pfn_notify)(const char*, const void*, size_t, void*),
    void* user_data,
    cl_int* errcode_ret);

CL_API_ENTRY cl_int CL_API_CALL clReleaseContext(cl_context context);

CL_API_ENTRY cl_command_queue CL_API_CALL clCreateCommandQueue(
    cl_context context,
    cl_device_id device,
    cl_command_queue_properties properties,
    cl_int* errcode_ret);

CL_API_ENTRY cl_int CL_API_CALL clReleaseCommandQueue(cl_command_queue command_queue);

CL_API_ENTRY cl_program CL_API_CALL clCreateProgramWithSource(
    cl_context context,
    cl_uint count,
    const char** strings,
    const size_t* lengths,
    cl_int* errcode_ret);

CL_API_ENTRY cl_int CL_API_CALL clBuildProgram(
    cl_program program,
    cl_uint num_devices,
    const cl_device_id* device_list,
    const char* options,
    void (CL_CALLBACK* pfn_notify)(cl_program, void*),
    void* user_data);

CL_API_ENTRY cl_int CL_API_CALL clGetProgramBuildInfo(
    cl_program program,
    cl_device_id device,
    cl_program_build_info param_name,
    size_t param_value_size,
    void* param_value,
    size_t* param_value_size_ret);

CL_API_ENTRY cl_int CL_API_CALL clReleaseProgram(cl_program program);

CL_API_ENTRY cl_kernel CL_API_CALL clCreateKernel(
    cl_program program,
    const char* kernel_name,
    cl_int* errcode_ret);

CL_API_ENTRY cl_int CL_API_CALL clReleaseKernel(cl_kernel kernel);

CL_API_ENTRY cl_mem CL_API_CALL clCreateBuffer(
    cl_context context,
    cl_mem_flags flags,
    size_t size,
    void* host_ptr,
    cl_int* errcode_ret);

CL_API_ENTRY cl_int CL_API_CALL clReleaseMemObject(cl_mem memobj);

CL_API_ENTRY cl_int CL_API_CALL clSetKernelArg(
    cl_kernel kernel,
    cl_uint arg_index,
    size_t arg_size,
    const void* arg_value);

CL_API_ENTRY cl_int CL_API_CALL clEnqueueNDRangeKernel(
    cl_command_queue command_queue,
    cl_kernel kernel,
    cl_uint work_dim,
    const size_t* global_work_offset,
    const size_t* global_work_size,
    const size_t* local_work_size,
    cl_uint num_events_in_wait_list,
    const cl_event* event_wait_list,
    cl_event* event);

CL_API_ENTRY cl_int CL_API_CALL clEnqueueReadBuffer(
    cl_command_queue command_queue,
    cl_mem buffer,
    cl_bool blocking_read,
    size_t offset,
    size_t size,
    void* ptr,
    cl_uint num_events_in_wait_list,
    const cl_event* event_wait_list,
    cl_event* event);

CL_API_ENTRY cl_int CL_API_CALL clFinish(cl_command_queue command_queue);

#ifdef __cplusplus
}
#endif
