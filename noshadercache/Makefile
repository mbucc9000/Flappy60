TARGET = iphone:clang:6.1:6.0
ARCHS = armv7

include $(THEOS)/makefiles/common.mk

# Built as a plain dylib in the VM (make FINALPACKAGE=1 -> .theos/obj/NoShaderCache.dylib); copy it next to this
# Makefile. build_flappy_deb.py adds it to the .app and loads it with a load command.
LIBRARY_NAME = NoShaderCache
NoShaderCache_FILES = NoShaderCache.c
NoShaderCache_CFLAGS = -Wall

include $(THEOS)/makefiles/library.mk
