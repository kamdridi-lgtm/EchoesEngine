__kernel void vector_add(__global const float* left,
                         __global const float* right,
                         __global float* output,
                         const unsigned int count) {
    const unsigned int index = get_global_id(0);
    if (index >= count) {
        return;
    }

    output[index] = left[index] + right[index];
}
