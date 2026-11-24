cmake_minimum_required(VERSION 3.10)

if(NOT DEFINED ENV{VITASDK})
  message(FATAL_ERROR "VITASDK is not set")
endif()
if(NOT DEFINED CMAKE_TOOLCHAIN_FILE)
  set(CMAKE_TOOLCHAIN_FILE "$ENV{VITASDK}/share/vita.toolchain.cmake")
endif()

project(f2nd60fps C)
include("$ENV{VITASDK}/share/vita.cmake" REQUIRED)

add_executable(f2nd60fps main.c)
target_compile_options(f2nd60fps PRIVATE -Wall -O2 -std=gnu99)
target_link_options(f2nd60fps PRIVATE -nostartfiles -Wl,-q)
target_link_libraries(f2nd60fps taihen_stub SceLibKernel_stub)

vita_create_self(f2nd60fps.suprx f2nd60fps CONFIG exports.yml UNSAFE)
