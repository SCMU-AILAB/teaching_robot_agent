cmake_minimum_required(VERSION 3.20)
project(robot_ui VERSION 1.0.0 LANGUAGES C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

include(FetchContent)
include(CTest)
find_package(CURL REQUIRED)
find_package(Threads REQUIRED)

set(BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(BUILD_GAMES OFF CACHE BOOL "" FORCE)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(ENABLE_CJSON_TEST OFF CACHE BOOL "" FORCE)
set(CJSON_BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)

FetchContent_Declare(raylib
    URL https://github.com/raysan5/raylib/archive/refs/tags/5.5.tar.gz
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_Declare(cjson
    URL https://github.com/DaveGamble/cJSON/archive/refs/tags/v1.7.18.tar.gz
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_Declare(raygui
    URL https://github.com/raysan5/raygui/archive/refs/tags/4.0.tar.gz
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_GetProperties(raylib)
if(NOT raylib_POPULATED)
    FetchContent_Populate(raylib)
endif()
# Raylib 默认 project() 会无谓地启用 CXX；上游源码均为 C，这里仅收窄构建语言。
file(READ ${raylib_SOURCE_DIR}/CMakeLists.txt raylib_cmake)
string(REPLACE "project(raylib)" "project(raylib C)" raylib_cmake "${raylib_cmake}")
file(WRITE ${raylib_SOURCE_DIR}/CMakeLists.txt "${raylib_cmake}")
add_subdirectory(${raylib_SOURCE_DIR} ${raylib_BINARY_DIR})
FetchContent_GetProperties(cjson)
if(NOT cjson_POPULATED)
    FetchContent_Populate(cjson)
endif()
add_library(robot_cjson STATIC ${cjson_SOURCE_DIR}/cJSON.c)
target_include_directories(robot_cjson PUBLIC ${cjson_SOURCE_DIR})
FetchContent_GetProperties(raygui)
if(NOT raygui_POPULATED)
    FetchContent_Populate(raygui)
endif()

add_library(robot_ui_core STATIC
    src/api_client.c
    src/config.c
    src/event_client.c
    src/state.c
    src/text.c)
target_include_directories(robot_ui_core PUBLIC include)
target_link_libraries(robot_ui_core PUBLIC CURL::libcurl Threads::Threads robot_cjson)
target_compile_definitions(robot_ui_core PUBLIC _POSIX_C_SOURCE=200809L)

add_executable(robot_ui
    src/main.c
    src/app.c
    src/camera.c
    src/face.c
    src/router.c
    src/theme.c
    src/ui_common.c
    src/screens/dashboard.c
    src/screens/assistant.c
    src/screens/vision.c
    src/screens/monitor.c)
target_include_directories(robot_ui PRIVATE include)
target_include_directories(robot_ui SYSTEM PRIVATE ${raygui_SOURCE_DIR}/src)
target_link_libraries(robot_ui PRIVATE robot_ui_core raylib)
if(UNIX AND NOT APPLE)
    target_link_libraries(robot_ui PRIVATE m)
endif()

if(CMAKE_C_COMPILER_ID MATCHES "GNU|Clang")
    target_compile_options(robot_ui_core PRIVATE -Wall -Wextra -Wpedantic -Werror)
    target_compile_options(robot_ui PRIVATE -Wall -Wextra -Wpedantic -Werror)
endif()

file(COPY config DESTINATION ${CMAKE_BINARY_DIR})
file(COPY assets DESTINATION ${CMAKE_BINARY_DIR})

if(BUILD_TESTING)
    foreach(test_name IN ITEMS test_config test_state test_text)
        add_executable(${test_name} tests/${test_name}.c)
        target_link_libraries(${test_name} PRIVATE robot_ui_core)
        add_test(NAME ${test_name} COMMAND ${test_name})
    endforeach()
endif()
