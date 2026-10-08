cmake_minimum_required(VERSION 3.16)
project(osm_map_builder LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_path(
  OSMIUM_INCLUDE_DIR
  osmium/io/reader.hpp
  HINTS ENV LIBOSMIUM_INCLUDE_DIR)
if(NOT OSMIUM_INCLUDE_DIR)
  message(FATAL_ERROR "libosmium headers were not found; install libosmium2-dev")
endif()

find_package(BZip2 REQUIRED)
find_package(EXPAT REQUIRED)
find_package(OpenSSL REQUIRED)
find_package(SQLite3 REQUIRED)
find_package(Threads REQUIRED)
find_package(ZLIB REQUIRED)

add_executable(osm_map_builder osm_map_builder.cpp)
add_executable(osm_dependency_builder osm_dependency_builder.cpp)
foreach(target osm_map_builder osm_dependency_builder)
  target_include_directories(${target} PRIVATE ${OSMIUM_INCLUDE_DIR})
  target_compile_options(${target} PRIVATE -O3 -Wall -Wextra -Wpedantic)
  target_link_libraries(
    ${target}
    PRIVATE BZip2::BZip2
            EXPAT::EXPAT
            OpenSSL::Crypto
            SQLite::SQLite3
            Threads::Threads
            ZLIB::ZLIB)
endforeach()

install(TARGETS osm_map_builder osm_dependency_builder RUNTIME DESTINATION bin)
