// Loaded by Flappy 60FPS through an LC_LOAD_DYLIB that build_flappy_deb.py adds to the game binary.
//
// The iOS 6 SGX GL driver keeps an exclusive flock on Caches/<bundle id>/com.apple.opengl/shaders.maps for as
// long as a GL context lives. /Applications apps use the shared /var/mobile Caches, which backboardd counts as a
// system file, so every home press killed the game (0xdead10cc, "suspended with locked system files"). 0 turns the
// cache off (the driver reads it when the game's EAGLView makes its context, long after this runs); shaders just
// compile in memory.
#include <stdlib.h>

__attribute__((constructor)) static void NoShaderCacheInit(void) {
    setenv("SGX_MAX_SHADER_CACHE_SIZE", "0", 1);
}
