#include <osmium/area/assembler.hpp>
#include <osmium/area/multipolygon_manager.hpp>
#include <osmium/geom/wkb.hpp>
#include <osmium/handler.hpp>
#include <osmium/handler/node_locations_for_ways.hpp>
#include <osmium/index/map/sparse_file_array.hpp>
#include <osmium/io/any_input.hpp>
#include <osmium/io/reader.hpp>
#include <osmium/relations/manager_util.hpp>
#include <osmium/tags/tags_filter.hpp>
#include <osmium/visitor.hpp>

#include <openssl/evp.h>
#include <sqlite3.h>

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <fcntl.h>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <sys/stat.h>
#include <unistd.h>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace fs = std::filesystem;

namespace {

constexpr const char* kSchemaVersion = "1";
constexpr double kEarthRadiusM = 6371000.0;

const std::array<const char*, 14> kPrimaryCategoryKeys = {
 "amenity", "shop", "tourism", "leisure", "office", "healthcare", "craft",
 "emergency", "public_transport", "railway", "aeroway", "historic",
 "man_made", "natural"};
const std::array<const char*, 6> kAreaCategoryKeys = {
 "building", "landuse", "natural", "leisure", "water", "waterway"};
const std::unordered_set<std::string> kDrivingHighways = {
 "motorway", "motorway_link", "trunk", "trunk_link", "primary",
 "primary_link", "secondary", "secondary_link", "tertiary", "tertiary_link",
 "unclassified", "residential", "living_street", "service", "road", "track"};
const std::unordered_set<std::string> kWalkingExcluded = {"motorway", "motorway_link"};
const std::unordered_set<std::string> kCyclingExcluded = {
 "motorway", "motorway_link", "steps"};
const std::unordered_set<std::string> kDeniedAccess = {"no", "private"};
const std::set<std::string> kExportTagKeys = {
 "addr:city", "addr:district", "addr:housenumber", "addr:postcode",
 "addr:province", "addr:street", "addr:subdistrict", "aeroway", "amenity",
 "brand", "building", "contact:phone", "contact:website", "craft", "emergency",
 "healthcare", "historic", "landuse", "leisure", "man_made", "name", "name:en",
 "name:zh", "natural", "office",
 "opening_hours", "official_name", "old_name", "phone", "place", "population",
 "public_transport", "railway", "shop", "short_name", "alt_name", "tourism",
 "website"};

const std::unordered_map<std::string, std::string> kCategoryAliases = {
 {"amenity|restaurant", "餐厅 餐馆 饭店 restaurant"},
 {"amenity|cafe", "咖啡 咖啡馆 cafe coffee"},
 {"amenity|fast_food", "快餐 fast food"},
 {"amenity|hospital", "医院 hospital"},
 {"amenity|clinic", "诊所 clinic"},
 {"amenity|pharmacy", "药店 药房 pharmacy"},
 {"amenity|school", "学校 school"},
 {"amenity|kindergarten", "幼儿园 kindergarten"},
 {"amenity|university", "大学 高校 university"},
 {"amenity|college", "学院 college"},
 {"amenity|bank", "银行 bank"},
 {"amenity|atm", "取款机 ATM atm"},
 {"amenity|fuel", "加油站 fuel gas station"},
 {"amenity|charging_station", "充电站 充电桩 charging"},
 {"amenity|parking", "停车场 parking"},
 {"amenity|toilets", "厕所 洗手间 toilets"},
 {"amenity|police", "派出所 公安 police"},
 {"amenity|post_office", "邮局 邮政 post office"},
 {"shop|convenience", "便利店 便利 convenience"},
 {"shop|supermarket", "超市 supermarket"},
 {"shop|mall", "商场 购物中心 mall"},
 {"shop|bakery", "面包店 烘焙 bakery"},
 {"shop|books", "书店 books"},
 {"tourism|hotel", "酒店 宾馆 hotel"},
 {"tourism|attraction", "景点 旅游景点 attraction"},
 {"tourism|museum", "博物馆 museum"},
 {"leisure|park", "公园 park"},
 {"leisure|sports_centre", "体育馆 体育中心 sports centre"},
 {"public_transport|station", "车站 公交站 station"},
 {"railway|station", "火车站 地铁站 railway station"},
 {"aeroway|aerodrome", "机场 airport"},
};

std::string utc_now() {
 std::time_t now = std::time(nullptr);
 std::tm value{};
 gmtime_r(&now, &value);
 char buffer[32]{};
 std::strftime(buffer, sizeof(buffer), "%Y-%m-%dT%H:%M:%SZ", &value);
 return buffer;
}

std::string json_escape(std::string_view value) {
 std::ostringstream output;
 for (const unsigned char character : value) {
 switch (character) {
 case '"': output << "\\\""; break;
 case '\\': output << "\\\\"; break;
 case '\b': output << "\\b"; break;
 case '\f': output << "\\f"; break;
 case '\n': output << "\\n"; break;
 case '\r': output << "\\r"; break;
 case '\t': output << "\\t"; break;
 default:
 if (character < 0x20) {
 output << "\\u" << std::hex << std::setw(4) << std::setfill('0')
 << static_cast<int>(character) << std::dec;
 } else {
 output << static_cast<char>(character);
 }
 }
 }
 return output.str();
}

std::string tag(const osmium::TagList& tags, const char* key) {
 const char* value = tags.get_value_by_key(key);
 return value == nullptr ? std::string{} : std::string{value};
}

bool contains(const std::unordered_set<std::string>& values, const std::string& value) {
 return values.find(value) != values.end();
}

std::vector<std::string> split_semicolon(const std::string& value) {
 std::vector<std::string> result;
 std::size_t start = 0;
 while (start <= value.size()) {
 const std::size_t end = value.find(';', start);
 const std::string part = value.substr(start, end == std::string::npos ? end : end - start);
 if (!part.empty()) result.push_back(part);
 if (end == std::string::npos) break;
 start = end + 1;
 }
 return result;
}

std::pair<std::string, std::string> names(const osmium::TagList& tags) {
 const std::array<const char*, 9> keys = {"name", "name:zh", "name:zh-Hans",
 "official_name", "short_name", "alt_name", "old_name", "name:en", "brand"};
 std::vector<std::string> values;
 for (const char* key : keys) {
 for (const auto& value : split_semicolon(tag(tags, key))) {
 if (std::find(values.begin(), values.end(), value) == values.end()) values.push_back(value);
 }
 }
 if (values.empty()) return {"", ""};
 std::ostringstream aliases;
 for (std::size_t index = 1; index < values.size(); ++index) {
 if (index > 1) aliases << '|';
 aliases << values[index];
 }
 return {values.front(), aliases.str()};
}

std::pair<std::string, std::string> address(const osmium::TagList& tags) {
 const std::string street = [&]() {
 const std::string primary = tag(tags, "addr:street");
 return primary.empty() ? tag(tags, "addr:place") : primary;
 }();
 const std::string display = street + tag(tags, "addr:housenumber");
 const std::string context = tag(tags, "addr:province") + tag(tags, "addr:city") +
 tag(tags, "addr:district") + tag(tags, "addr:subdistrict");
 return {display, context};
}

std::pair<std::string, std::string> primary_category(const osmium::TagList& tags) {
 for (const char* key : kPrimaryCategoryKeys) {
 const std::string value = tag(tags, key);
 if (!value.empty()) return {key, value};
 }
 return {"", ""};
}

int parse_integer(const std::string& value) {
 std::string filtered;
 for (const char character : value) {
 if ((character >= '0' && character <= '9') || (character == '-' && filtered.empty())) {
 filtered.push_back(character);
 }
 }
 if (filtered.empty() || filtered == "-") return 0;
 try { return std::stoi(filtered); } catch (...) { return 0; }
}

double parse_speed(const std::string& value) {
 const char* begin = value.c_str();
 char* end = nullptr;
 const double parsed = std::strtod(begin, &end);
 if (end == begin || parsed <= 0) return 0.0;
 const bool mph = value.find("mph") != std::string::npos || value.find("MPH") != std::string::npos;
 return std::min(200.0, parsed * (mph ? 1.609344 : 1.0));
}

std::string export_tags(const osmium::TagList& tags) {
 std::ostringstream output;
 output << '{';
 bool first = true;
 for (const auto& key : kExportTagKeys) {
 const std::string value = tag(tags, key.c_str());
 if (value.empty()) continue;
 if (!first) output << ',';
 first = false;
 output << '"' << json_escape(key) << "\":\"" << json_escape(value) << '"';
 }
 output << '}';
 return output.str();
}

std::size_t utf8_width(unsigned char first) {
 if ((first & 0x80U) == 0) return 1;
 if ((first & 0xE0U) == 0xC0U) return 2;
 if ((first & 0xF0U) == 0xE0U) return 3;
 if ((first & 0xF8U) == 0xF0U) return 4;
 return 1;
}

std::uint32_t utf8_codepoint(std::string_view value, std::size_t offset, std::size_t width) {
 const auto byte = [&](std::size_t index) { return static_cast<unsigned char>(value[offset + index]); };
 if (width == 1) return byte(0);
 if (width == 2) return ((byte(0) & 0x1FU) << 6U) | (byte(1) & 0x3FU);
 if (width == 3) return ((byte(0) & 0x0FU) << 12U) | ((byte(1) & 0x3FU) << 6U) |
 (byte(2) & 0x3FU);
 return ((byte(0) & 0x07U) << 18U) | ((byte(1) & 0x3FU) << 12U) |
 ((byte(2) & 0x3FU) << 6U) | (byte(3) & 0x3FU);
}

std::string normalize_text(std::string value) {
 bool previous_space = true;
 std::string result;
 result.reserve(value.size());
 for (unsigned char character : value) {
 if (character < 0x80 && std::isspace(character)) {
 if (!previous_space) result.push_back(' ');
 previous_space = true;
 } else {
 if (character >= 'A' && character <= 'Z') character = static_cast<unsigned char>(character + 32);
 result.push_back(static_cast<char>(character));
 previous_space = false;
 }
 }
 if (!result.empty() && result.back() == ' ') result.pop_back();
 return result;
}

std::vector<std::string> search_tokens(const std::string& value) {
 std::vector<std::string> output;
 std::unordered_set<std::string> seen;
 const std::string normalized = normalize_text(value);
 std::size_t index = 0;
 while (index < normalized.size()) {
 const unsigned char first = static_cast<unsigned char>(normalized[index]);
 if (first < 0x80 && std::isalnum(first)) {
 std::size_t end = index + 1;
 while (end < normalized.size()) {
 const unsigned char character = static_cast<unsigned char>(normalized[end]);
 if (character >= 0x80 || !std::isalnum(character)) break;
 ++end;
 }
 std::string token = normalized.substr(index, end - index);
 if (seen.insert(token).second) output.push_back(std::move(token));
 index = end;
 continue;
 }
 const std::size_t width = std::min(utf8_width(first), normalized.size() - index);
 const std::uint32_t codepoint = utf8_codepoint(normalized, index, width);
 if (codepoint >= 0x3400 && codepoint <= 0x9FFF) {
 std::vector<std::string> run;
 while (index < normalized.size()) {
 const std::size_t current_width = std::min(
 utf8_width(static_cast<unsigned char>(normalized[index])), normalized.size() - index);
 const std::uint32_t current = utf8_codepoint(normalized, index, current_width);
 if (current < 0x3400 || current > 0x9FFF) break;
 run.push_back(normalized.substr(index, current_width));
 index += current_width;
 }
 if (run.size() == 1) {
 if (seen.insert(run[0]).second) output.push_back(run[0]);
 } else {
 for (std::size_t position = 0; position + 1 < run.size(); ++position) {
 std::string token = run[position] + run[position + 1];
 if (seen.insert(token).second) output.push_back(std::move(token));
 }
 }
 continue;
 }
 index += width;
 }
 return output;
}

std::string search_document(const std::vector<std::string>& values) {
 std::ostringstream raw_stream;
 bool first = true;
 for (const auto& value : values) {
 if (value.empty()) continue;
 if (!first) raw_stream << ' ';
 first = false;
 raw_stream << normalize_text(value);
 }
 const std::string raw = raw_stream.str();
 std::ostringstream output;
 output << raw;
 for (const auto& token : search_tokens(raw)) output << ' ' << token;
 return output.str();
}

std::string category_aliases(const std::string& category, const std::string& subcategory) {
 const auto found = kCategoryAliases.find(category + '|' + subcategory);
 return found == kCategoryAliases.end() ? std::string{} : found->second;
}

double point_distance_m(double a_lat, double a_lon, double b_lat, double b_lon) {
 const double mean_lat = (a_lat + b_lat) * M_PI / 360.0;
 const double dx = (b_lon - a_lon) * M_PI / 180.0 * std::cos(mean_lat);
 const double dy = (b_lat - a_lat) * M_PI / 180.0;
return kEarthRadiusM * std::hypot(dx, dy);
}

std::string box_json(const osmium::Box& box) {
 if (!box.valid()) return "{}";
 std::ostringstream output;
 output << std::setprecision(10)
 << "{\"min_latitude\":" << box.bottom_left().lat()
 << ",\"min_longitude\":" << box.bottom_left().lon()
 << ",\"max_latitude\":" << box.top_right().lat()
 << ",\"max_longitude\":" << box.top_right().lon() << '}';
 return output.str();
}

std::pair<int, int> route_modes(const osmium::TagList& tags) {
 const std::string highway = tag(tags, "highway");
 if (highway.empty()) return {0, 0};
 const std::string general = tag(tags, "access");
 const std::string foot = tag(tags, "foot");
 const std::string bicycle = tag(tags, "bicycle");
 std::string motor = tag(tags, "motor_vehicle");
 if (motor.empty()) motor = tag(tags, "motorcar");
 const auto allowed = [](const std::string& value) {
 return value == "yes" || value == "designated" || value == "permissive";
 };
 const bool walking = (!contains(kWalkingExcluded, highway) && !contains(kDeniedAccess, general) &&
 !contains(kDeniedAccess, foot)) || allowed(foot);
 const bool cycling = (!contains(kCyclingExcluded, highway) && !contains(kDeniedAccess, general) &&
 !contains(kDeniedAccess, bicycle)) || allowed(bicycle);
 const bool driving = (contains(kDrivingHighways, highway) && !contains(kDeniedAccess, general) &&
 !contains(kDeniedAccess, motor)) || allowed(motor);
 const int both = (walking ? 1 : 0) | (cycling ? 2 : 0) | (driving ? 4 : 0);
 int forward = both;
 int backward = both;
 std::string oneway = tag(tags, "oneway");
 if (oneway.empty() && tag(tags, "junction") == "roundabout") oneway = "yes";
 const int directed = (cycling ? 2 : 0) | (driving ? 4 : 0);
 if (oneway == "yes" || oneway == "1" || oneway == "true") backward &= ~directed;
 if (oneway == "-1") forward &= ~directed;
 const std::string bicycle_oneway = tag(tags, "oneway:bicycle");
 if (cycling && (bicycle_oneway == "no" || bicycle_oneway == "0" || bicycle_oneway == "false")) {
 forward |= 2;
 backward |= 2;
 }
 return {forward, backward};
}

std::string sha256_file(const fs::path& path) {
std::ifstream stream(path, std::ios::binary);
if (!stream) throw std::runtime_error("cannot open source for SHA256: " + path.string());
 std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)> context{
 EVP_MD_CTX_new(), EVP_MD_CTX_free};
 if (!context || EVP_DigestInit_ex(context.get(), EVP_sha256(), nullptr) != 1) {
 throw std::runtime_error("cannot initialize SHA256");
 }
std::vector<char> buffer(8 * 1024 * 1024);
while (stream) {
stream.read(buffer.data(), static_cast<std::streamsize>(buffer.size()));
const auto count = stream.gcount();
 if (count > 0 && EVP_DigestUpdate(
 context.get(), buffer.data(), static_cast<std::size_t>(count)) != 1) {
 throw std::runtime_error("cannot update SHA256");
 }
}
 std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
 unsigned int digest_size = 0;
 if (EVP_DigestFinal_ex(context.get(), digest.data(), &digest_size) != 1) {
 throw std::runtime_error("cannot finalize SHA256");
 }
std::ostringstream output;
 for (unsigned int index = 0; index < digest_size; ++index) {
 output << std::hex << std::setw(2) << std::setfill('0')
 << static_cast<int>(digest[index]);
 }
return output.str();
}

class Database;

class Statement {
 sqlite3_stmt* statement_ = nullptr;
 Database* database_ = nullptr;

public:
 Statement() = default;
 Statement(Database& database, const char* sql);
 Statement(const Statement&) = delete;
 Statement& operator=(const Statement&) = delete;
 Statement(Statement&& other) noexcept : statement_(other.statement_), database_(other.database_) {
 other.statement_ = nullptr;
 }
 ~Statement() { if (statement_ != nullptr) sqlite3_finalize(statement_); }

 void integer(int index, std::int64_t value) { sqlite3_bind_int64(statement_, index, value); }
 void real(int index, double value) { sqlite3_bind_double(statement_, index, value); }
 void text(int index, const std::string& value) {
 sqlite3_bind_text(statement_, index, value.data(), static_cast<int>(value.size()), SQLITE_TRANSIENT);
 }
 void blob(int index, const std::string& value) {
 if (value.empty()) sqlite3_bind_null(statement_, index);
 else sqlite3_bind_blob(statement_, index, value.data(), static_cast<int>(value.size()), SQLITE_TRANSIENT);
 }
 void run();
 sqlite3_stmt* get() { return statement_; }
};

class Database {
 sqlite3* database_ = nullptr;

public:
 explicit Database(const fs::path& path) {
 if (sqlite3_open(path.c_str(), &database_) != SQLITE_OK) {
 const std::string message = database_ ? sqlite3_errmsg(database_) : "unknown SQLite error";
 if (database_) sqlite3_close(database_);
 throw std::runtime_error("cannot create SQLite database: " + message);
 }
 sqlite3_busy_timeout(database_, 30000);
 }
 Database(const Database&) = delete;
 Database& operator=(const Database&) = delete;
 ~Database() { if (database_ != nullptr) sqlite3_close(database_); }

 sqlite3* get() { return database_; }
 void exec(const std::string& sql) {
 char* error = nullptr;
 if (sqlite3_exec(database_, sql.c_str(), nullptr, nullptr, &error) != SQLITE_OK) {
 const std::string message = error == nullptr ? sqlite3_errmsg(database_) : error;
 sqlite3_free(error);
 throw std::runtime_error("SQLite error: " + message + " SQL=" + sql);
 }
 }
 std::int64_t scalar_integer(const std::string& sql) {
 Statement statement{*this, sql.c_str()};
 const int result = sqlite3_step(statement.get());
 if (result != SQLITE_ROW) throw std::runtime_error("SQLite scalar query failed: " + sql);
 return sqlite3_column_int64(statement.get(), 0);
 }
 std::string scalar_text(const std::string& sql) {
 Statement statement{*this, sql.c_str()};
 const int result = sqlite3_step(statement.get());
 if (result != SQLITE_ROW) throw std::runtime_error("SQLite scalar query failed: " + sql);
 const unsigned char* value = sqlite3_column_text(statement.get(), 0);
 return value == nullptr ? std::string{} : reinterpret_cast<const char*>(value);
 }
};

Statement::Statement(Database& database, const char* sql) : database_(&database) {
 if (sqlite3_prepare_v2(database.get(), sql, -1, &statement_, nullptr) != SQLITE_OK) {
 throw std::runtime_error("cannot prepare SQLite statement: " +
 std::string(sqlite3_errmsg(database.get())) + " SQL=" + sql);
 }
}

void Statement::run() {
 const int result = sqlite3_step(statement_);
 if (result != SQLITE_DONE) {
 throw std::runtime_error("SQLite statement failed: " +
 std::string(sqlite3_errmsg(database_->get())));
 }
 sqlite3_reset(statement_);
 sqlite3_clear_bindings(statement_);
}

const char* kSchema = R"SQL(
CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE features(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 osm_type TEXT NOT NULL,osm_id INTEGER NOT NULL,feature_type TEXT NOT NULL,
 name TEXT NOT NULL DEFAULT '',aliases TEXT NOT NULL DEFAULT '',brand TEXT NOT NULL DEFAULT '',
 category TEXT NOT NULL DEFAULT '',subcategory TEXT NOT NULL DEFAULT '',
 admin_level INTEGER NOT NULL DEFAULT 0,population INTEGER NOT NULL DEFAULT 0,
 address TEXT NOT NULL DEFAULT '',admin_context TEXT NOT NULL DEFAULT '',postcode TEXT NOT NULL DEFAULT '',
 phone TEXT NOT NULL DEFAULT '',website TEXT NOT NULL DEFAULT '',opening_hours TEXT NOT NULL DEFAULT '',
 lat REAL NOT NULL,lon REAL NOT NULL,min_lat REAL NOT NULL,min_lon REAL NOT NULL,
 max_lat REAL NOT NULL,max_lon REAL NOT NULL,geom BLOB,tags_json TEXT NOT NULL DEFAULT '{}',
 search_text TEXT NOT NULL);
CREATE TABLE render_areas(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 osm_type TEXT NOT NULL,osm_id INTEGER NOT NULL,
 category TEXT NOT NULL,subcategory TEXT NOT NULL DEFAULT '',
 min_lat REAL NOT NULL,min_lon REAL NOT NULL,max_lat REAL NOT NULL,max_lon REAL NOT NULL,
 geom BLOB NOT NULL,UNIQUE(osm_type,osm_id));
CREATE TABLE route_nodes(
 id INTEGER PRIMARY KEY,lat REAL NOT NULL,lon REAL NOT NULL,modes INTEGER NOT NULL);
CREATE TABLE route_edges(
 way_id INTEGER NOT NULL,seq INTEGER NOT NULL,source INTEGER NOT NULL,target INTEGER NOT NULL,
 length_m REAL NOT NULL,forward_modes INTEGER NOT NULL,backward_modes INTEGER NOT NULL,
 name TEXT NOT NULL DEFAULT '',highway TEXT NOT NULL DEFAULT '',speed_kph REAL NOT NULL DEFAULT 0);
)SQL";

struct FeatureInput {
 std::string osm_type;
 std::int64_t osm_id = 0;
 std::string feature_type;
 const osmium::TagList* tags = nullptr;
 double lat = 0;
 double lon = 0;
 double min_lat = 0;
 double min_lon = 0;
 double max_lat = 0;
 double max_lon = 0;
 std::string geometry;
 std::string category;
 std::string subcategory;
 std::string name_override;
};

class ImportHandler : public osmium::handler::Handler {
 Database& database_;
Statement feature_;
 Statement render_area_;
Statement route_edge_;
osmium::geom::WKBFactory<> wkb_;
 osmium::Box bounds_;
 std::unordered_map<std::int64_t, std::uint8_t> route_modes_;
 bool route_modes_reserved_ = false;
 std::size_t batch_size_;
 std::size_t pending_ = 0;
 std::uint64_t objects_ = 0;
 std::uint64_t feature_count_ = 0;
 std::uint64_t edge_count_ = 0;
 std::chrono::steady_clock::time_point started_ = std::chrono::steady_clock::now();

 void touch() {
 if (++pending_ < batch_size_) return;
 database_.exec("COMMIT;BEGIN");
 pending_ = 0;
 }

 void progress() {
 ++objects_;
 if (objects_ % 1000000 != 0) return;
 const auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(
 std::chrono::steady_clock::now() - started_).count();
 std::cout << "objects=" << objects_ << " features=" << feature_count_
 << " edges=" << edge_count_ << " elapsed=" << elapsed << "s\n" << std::flush;
 }

public:
 ImportHandler(Database& database, std::size_t batch_size)
 : database_(database),
feature_(database, "INSERT INTO features(osm_type,osm_id,feature_type,name,aliases,brand,"
 "category,subcategory,admin_level,population,address,admin_context,postcode,phone,website,"
 "opening_hours,lat,lon,min_lat,min_lon,max_lat,max_lon,geom,tags_json,search_text)"
 " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"),
 render_area_(database, "INSERT INTO render_areas(osm_type,osm_id,category,subcategory,"
 "min_lat,min_lon,max_lat,max_lon,geom) VALUES(?,?,?,?,?,?,?,?,?)"),
 route_edge_(database, "INSERT INTO route_edges(way_id,seq,source,target,length_m,"
 "forward_modes,backward_modes,name,highway,speed_kph) VALUES(?,?,?,?,?,?,?,?,?,?)"),
 batch_size_(batch_size) {
 database_.exec("BEGIN");
 }

void finish() {
database_.exec("COMMIT");
pending_ = 0;
}

 template <typename TLocationIndex>
 void write_route_nodes(TLocationIndex& location_index) {
 std::cout << "route node phase: sorting " << route_modes_.size()
 << " unique nodes\n" << std::flush;
 std::vector<std::pair<std::int64_t, std::uint8_t>> nodes;
 nodes.reserve(route_modes_.size());
 for (const auto& value : route_modes_) nodes.push_back(value);
 route_modes_.clear();
 route_modes_.rehash(0);
 std::sort(nodes.begin(), nodes.end(), [](const auto& left, const auto& right) {
 return left.first < right.first;
 });
 Statement insert{database_, "INSERT INTO route_nodes(id,lat,lon,modes) VALUES(?,?,?,?)"};
 database_.exec("BEGIN");
 auto location = location_index.begin();
 const auto location_end = location_index.end();
 std::size_t written = 0;
 for (const auto& [id, modes] : nodes) {
 if (id < 0) continue;
 const auto unsigned_id = static_cast<osmium::unsigned_object_id_type>(id);
 while (location != location_end && location->first < unsigned_id) ++location;
 if (location == location_end) break;
 if (location->first != unsigned_id || !location->second.valid()) continue;
 insert.integer(1, id);
 insert.real(2, location->second.lat());
 insert.real(3, location->second.lon());
 insert.integer(4, modes);
 insert.run();
 if (++written % 500000 == 0) {
 database_.exec("COMMIT;BEGIN");
 std::cout << "route nodes=" << written << '\n' << std::flush;
 }
 }
 database_.exec("COMMIT");
 std::cout << "route node phase: complete nodes=" << written << '\n' << std::flush;
 }

std::uint64_t object_count() const { return objects_; }
 std::string bounds_json() const { return box_json(bounds_); }

 void add_feature(FeatureInput input) {
 auto [name, aliases] = names(*input.tags);
 if (!input.name_override.empty()) name = input.name_override;
 const std::string brand = tag(*input.tags, "brand");
 auto [display_address, admin_context] = address(*input.tags);
 if (input.feature_type == "address" && name.empty()) name = display_address;
 if (name.empty() && input.feature_type != "boundary") return;
 const std::string document = search_document({name, aliases, brand, display_address,
 admin_context, input.category, input.subcategory,
 category_aliases(input.category, input.subcategory)});
 int index = 1;
 feature_.text(index++, input.osm_type);
 feature_.integer(index++, input.osm_id);
 feature_.text(index++, input.feature_type);
 feature_.text(index++, name);
 feature_.text(index++, aliases);
 feature_.text(index++, brand);
 feature_.text(index++, input.category);
 feature_.text(index++, input.subcategory);
 feature_.integer(index++, parse_integer(tag(*input.tags, "admin_level")));
 feature_.integer(index++, parse_integer(tag(*input.tags, "population")));
 feature_.text(index++, display_address);
 feature_.text(index++, admin_context);
 feature_.text(index++, tag(*input.tags, "addr:postcode"));
 std::string phone = tag(*input.tags, "contact:phone");
 if (phone.empty()) phone = tag(*input.tags, "phone");
 feature_.text(index++, phone);
 std::string website = tag(*input.tags, "contact:website");
 if (website.empty()) website = tag(*input.tags, "website");
 feature_.text(index++, website);
 feature_.text(index++, tag(*input.tags, "opening_hours"));
 feature_.real(index++, input.lat);
 feature_.real(index++, input.lon);
 feature_.real(index++, input.min_lat);
 feature_.real(index++, input.min_lon);
 feature_.real(index++, input.max_lat);
 feature_.real(index++, input.max_lon);
 feature_.blob(index++, input.geometry);
 feature_.text(index++, export_tags(*input.tags));
 feature_.text(index++, document);
 feature_.run();
 ++feature_count_;
 touch();
 }

void node(const osmium::Node& node) {
progress();
if (!node.location().valid()) return;
 bounds_.extend(node.location());
 const double lat = node.location().lat();
 const double lon = node.location().lon();
 const std::string place = tag(node.tags(), "place");
 if (!place.empty()) add_feature({"node", node.id(), "place", &node.tags(), lat, lon,
 lat, lon, lat, lon, "", "place", place, ""});
 const auto [category, subcategory] = primary_category(node.tags());
 if (!category.empty()) add_feature({"node", node.id(), "poi", &node.tags(), lat, lon,
 lat, lon, lat, lon, "", category, subcategory, ""});
 if (!tag(node.tags(), "addr:housenumber").empty() || !tag(node.tags(), "addr:street").empty()) {
 add_feature({"node", node.id(), "address", &node.tags(), lat, lon,
 lat, lon, lat, lon, "", "address", "house", ""});
 }
 }

 void way(const osmium::Way& way) {
 progress();
 if (way.nodes().empty()) return;
 double lat_sum = 0;
 double lon_sum = 0;
 double min_lat = 90;
 double min_lon = 180;
 double max_lat = -90;
 double max_lon = -180;
 std::size_t valid = 0;
 for (const auto& node : way.nodes()) {
 if (!node.location().valid()) continue;
 const double lat = node.location().lat();
 const double lon = node.location().lon();
 lat_sum += lat;
 lon_sum += lon;
 min_lat = std::min(min_lat, lat);
 min_lon = std::min(min_lon, lon);
 max_lat = std::max(max_lat, lat);
 max_lon = std::max(max_lon, lon);
 ++valid;
 }
 if (valid == 0) return;
 const double lat = lat_sum / static_cast<double>(valid);
 const double lon = lon_sum / static_cast<double>(valid);
 std::string geometry;
 if (valid >= 2 && valid == way.nodes().size()) {
 try { geometry = wkb_.create_linestring(way); }
 catch (const osmium::geometry_error&) {}
 catch (const osmium::invalid_location&) {}
 }
 const std::string highway = tag(way.tags(), "highway");
 std::string road_name = names(way.tags()).first;
 if (road_name.empty()) road_name = tag(way.tags(), "ref");
 if (!highway.empty() && !road_name.empty()) {
 add_feature({"way", way.id(), "road", &way.tags(), lat, lon, min_lat, min_lon,
 max_lat, max_lon, geometry, "highway", highway, road_name});
 }
 const auto [category, subcategory] = primary_category(way.tags());
 if (!category.empty()) add_feature({"way", way.id(), "poi", &way.tags(), lat, lon,
 min_lat, min_lon, max_lat, max_lon, "", category, subcategory, ""});
 if (!tag(way.tags(), "addr:housenumber").empty()) {
 add_feature({"way", way.id(), "address", &way.tags(), lat, lon, min_lat, min_lon,
 max_lat, max_lon, "", "address", "house", ""});
 }
const auto [forward, backward] = route_modes(way.tags());
if ((forward == 0 && backward == 0) || way.nodes().size() < 2) return;
const int modes = forward | backward;
 if (!route_modes_reserved_) {
 route_modes_.max_load_factor(0.8F);
 route_modes_.reserve(50000000);
 route_modes_reserved_ = true;
 }
const double speed = parse_speed(tag(way.tags(), "maxspeed"));
 for (std::size_t sequence = 0; sequence + 1 < way.nodes().size(); ++sequence) {
 const auto& source = way.nodes()[sequence];
 const auto& target = way.nodes()[sequence + 1];
 if (!source.location().valid() || !target.location().valid() || source.ref() == target.ref()) continue;
 const double length = point_distance_m(source.location().lat(), source.location().lon(),
 target.location().lat(), target.location().lon());
 if (length < 0.1 || length > 100000.0) continue;
 auto merge_mode = [&](const std::int64_t id) {
 const auto [iterator, inserted] = route_modes_.try_emplace(
 id, static_cast<std::uint8_t>(modes));
 if (!inserted) iterator->second = static_cast<std::uint8_t>(iterator->second | modes);
 };
 merge_mode(source.ref());
 merge_mode(target.ref());
 route_edge_.integer(1, way.id());
 route_edge_.integer(2, static_cast<std::int64_t>(sequence));
 route_edge_.integer(3, source.ref());
 route_edge_.integer(4, target.ref());
 route_edge_.real(5, length);
 route_edge_.integer(6, forward);
 route_edge_.integer(7, backward);
 route_edge_.text(8, road_name);
 route_edge_.text(9, highway);
 route_edge_.real(10, speed);
 route_edge_.run();
 ++edge_count_;
 touch();
 }
 }

 void area(const osmium::Area& area) {
 progress();
 const bool boundary = tag(area.tags(), "boundary") == "administrative";
 std::string category;
 std::string subcategory;
 for (const char* key : kAreaCategoryKeys) {
 const std::string value = tag(area.tags(), key);
 if (!value.empty()) { category = key; subcategory = (value == "yes" || value == "no" || value == "true" || value == "false") ? key : value; break; }
 }
 if (!boundary && category.empty()) return;
 double lat_sum = 0;
 double lon_sum = 0;
 double min_lat = 90;
 double min_lon = 180;
 double max_lat = -90;
 double max_lon = -180;
 std::size_t count = 0;
 bool complete_geometry = true;
 for (const auto& ring : area.outer_rings()) {
 for (const auto& node : ring) {
 if (!node.location().valid()) {
 complete_geometry = false;
 continue;
 }
 const double lat = node.location().lat();
 const double lon = node.location().lon();
 lat_sum += lat;
 lon_sum += lon;
 min_lat = std::min(min_lat, lat);
 min_lon = std::min(min_lon, lon);
 max_lat = std::max(max_lat, lat);
 max_lon = std::max(max_lon, lon);
 ++count;
 }
 }
 if (count == 0 || !complete_geometry) return;
 std::string geometry;
 try { geometry = wkb_.create_multipolygon(area); }
 catch (const osmium::geometry_error&) { return; }
 catch (const osmium::invalid_location&) { return; }
 if (!category.empty()) {
 int index = 1;
 render_area_.text(index++, area.from_way() ? "way" : "relation");
 render_area_.integer(index++, area.orig_id());
 render_area_.text(index++, category);
 render_area_.text(index++, subcategory);
 render_area_.real(index++, min_lat);
 render_area_.real(index++, min_lon);
 render_area_.real(index++, max_lat);
 render_area_.real(index++, max_lon);
 render_area_.blob(index++, geometry);
 render_area_.run();
 touch();
 }
 if (boundary) {
 category = "boundary";
 subcategory = "admin_level_" + std::to_string(parse_integer(tag(area.tags(), "admin_level")));
 }
 add_feature({area.from_way() ? "way" : "relation", area.orig_id(),
 boundary ? "boundary" : "area", &area.tags(), lat_sum / count, lon_sum / count,
 min_lat, min_lon, max_lat, max_lon, std::move(geometry), category, subcategory, ""});
 }
};

void set_metadata(Database& database, const std::map<std::string, std::string>& values) {
 Statement statement{database, "INSERT INTO metadata(key,value) VALUES(?,?) "
 "ON CONFLICT(key) DO UPDATE SET value=excluded.value"};
 for (const auto& [key, value] : values) {
 statement.text(1, key);
 statement.text(2, value);
 statement.run();
 }
}

std::map<std::string, std::string> source_metadata(const fs::path& source) {
 osmium::io::File file{source.string()};
 osmium::io::Reader reader{file, osmium::osm_entity_bits::nothing};
 const osmium::io::Header header = reader.header();
 reader.close();
 const osmium::Box box = header.box();
 struct stat status{};
 if (::stat(source.c_str(), &status) != 0) throw std::system_error(errno, std::generic_category());
return {
 {"source_name", fs::absolute(source).string()},
 {"source_size", std::to_string(status.st_size)},
 {"source_mtime_ns", std::to_string(status.st_mtim.tv_sec * 1000000000LL + status.st_mtim.tv_nsec)},
 {"source_sha256", sha256_file(source)},
 {"replication_timestamp", header.get("osmosis_replication_timestamp")},
 {"replication_sequence", header.get("osmosis_replication_sequence_number")},
 {"replication_base_url", header.get("osmosis_replication_base_url")},
 {"bounds", box_json(box)},
 };
}

void build_indexes(Database& database) {
 const std::vector<std::string> statements = {
 "CREATE UNIQUE INDEX feature_identity ON features(osm_type,osm_id,feature_type)",
 "CREATE INDEX feature_kind ON features(feature_type,category,subcategory)",
 "CREATE INDEX render_area_kind ON render_areas(category,subcategory)",
"CREATE INDEX route_edges_source ON route_edges(source)",
 "CREATE INDEX route_edges_target ON route_edges(target)",
 "CREATE VIRTUAL TABLE features_rtree USING rtree(id,min_lat,max_lat,min_lon,max_lon)",
 "INSERT INTO features_rtree SELECT id,min_lat,max_lat,min_lon,max_lon FROM features",
 "CREATE VIRTUAL TABLE render_areas_rtree USING rtree(id,min_lat,max_lat,min_lon,max_lon)",
 "INSERT INTO render_areas_rtree SELECT id,min_lat,max_lat,min_lon,max_lon FROM render_areas",
 "CREATE VIRTUAL TABLE feature_fts USING fts5(search_text,tokenize='unicode61 remove_diacritics 2')",
 "INSERT INTO feature_fts(rowid,search_text) SELECT id,search_text FROM features",
 "CREATE VIRTUAL TABLE route_nodes_rtree USING rtree(id,min_lat,max_lat,min_lon,max_lon)",
 "INSERT INTO route_nodes_rtree SELECT id,lat,lat,lon,lon FROM route_nodes",
 "INSERT INTO feature_fts(feature_fts) VALUES('optimize')",
 "ANALYZE",
 };
 for (const auto& statement : statements) {
 std::istringstream words(statement);
 std::string preview;
 for (int count = 0; count < 5; ++count) {
 std::string word;
 if (!(words >> word)) break;
 if (!preview.empty()) preview += ' ';
 preview += word;
 }
 std::cout << "index phase: " << preview << '\n' << std::flush;
 database.exec(statement);
 }
}

std::map<std::string, std::string> validate(Database& database) {
 if (database.scalar_text("PRAGMA quick_check") != "ok") {
 throw std::runtime_error("SQLite quick_check failed");
 }
 const std::vector<std::pair<std::string, std::string>> queries = {
 {"feature_count", "SELECT count(*) FROM features"},
 {"render_area_count", "SELECT count(*) FROM render_areas"},
 {"route_node_count", "SELECT count(*) FROM route_nodes"},
 {"route_edge_count", "SELECT count(*) FROM route_edges"},
 {"boundary_count", "SELECT count(*) FROM features WHERE feature_type='boundary'"},
 {"place_count", "SELECT count(*) FROM features WHERE feature_type='place'"},
 {"poi_count", "SELECT count(*) FROM features WHERE feature_type='poi'"},
 {"road_count", "SELECT count(*) FROM features WHERE feature_type='road'"},
 {"address_count", "SELECT count(*) FROM features WHERE feature_type='address'"},
 };
 std::map<std::string, std::string> counts;
 for (const auto& [name, sql] : queries) {
 const auto count = database.scalar_integer(sql);
 counts[name] = std::to_string(count);
 if (name != "address_count" && name != "render_area_count" && count <= 0) {
 throw std::runtime_error("required layer is empty: " + name);
 }
 }
 if (database.scalar_integer("SELECT count(*) FROM features_rtree") !=
 std::stoll(counts["feature_count"]) ||
 database.scalar_integer("SELECT count(*) FROM feature_fts") !=
 std::stoll(counts["feature_count"]) ||
 database.scalar_integer("SELECT count(*) FROM render_areas_rtree") !=
 std::stoll(counts["render_area_count"]) ||
 database.scalar_integer("SELECT count(*) FROM route_nodes_rtree") !=
 std::stoll(counts["route_node_count"])) {
 throw std::runtime_error("index row counts do not match base tables");
 }
 if (database.scalar_integer("SELECT count(*) FROM feature_fts WHERE feature_fts MATCH '\"上海\"'") <= 0) {
 throw std::runtime_error("representative Chinese FTS query returned no results");
 }
 return counts;
}

struct Arguments {
 fs::path source;
 fs::path output;
 fs::path temp_dir = "/tmp/osm-map-build";
 std::size_t batch_size = 100000;
 bool keep_temp = false;
};

Arguments parse_arguments(int argc, char** argv) {
 if (argc < 3) {
 throw std::runtime_error("usage: osm_map_builder SOURCE.osm.pbf OUTPUT.sqlite "
 "[--temp-dir DIR] [--batch-size N] [--keep-temp]");
 }
 Arguments result{argv[1], argv[2]};
 for (int index = 3; index < argc; ++index) {
 const std::string option = argv[index];
 if (option == "--temp-dir" && index + 1 < argc) result.temp_dir = argv[++index];
 else if (option == "--batch-size" && index + 1 < argc) {
 result.batch_size = std::clamp<std::size_t>(std::stoull(argv[++index]), 1000, 1000000);
 } else if (option == "--keep-temp") result.keep_temp = true;
 else throw std::runtime_error("unknown or incomplete option: " + option);
 }
 return result;
}

int run(const Arguments& arguments) {
 if (!fs::is_regular_file(arguments.source)) throw std::runtime_error("source PBF does not exist");
 if (fs::exists(arguments.output)) throw std::runtime_error("output database already exists");
 fs::create_directories(arguments.output.parent_path());
 fs::create_directories(arguments.temp_dir);
 const fs::path location_path = arguments.temp_dir / (arguments.output.filename().string() + ".locations.idx");
 fs::remove(location_path);
 auto metadata = source_metadata(arguments.source);
 std::cout << "source=" << fs::absolute(arguments.source) << " size="
 << std::fixed << std::setprecision(2)
 << static_cast<double>(fs::file_size(arguments.source)) / (1024.0 * 1024.0 * 1024.0)
 << "GiB replication=" << metadata["replication_timestamp"] << '\n' << std::flush;
 Database database{arguments.output};
 database.exec("PRAGMA journal_mode=OFF;PRAGMA synchronous=OFF;PRAGMA temp_store=FILE;"
 "PRAGMA cache_size=-1048576;PRAGMA locking_mode=EXCLUSIVE");
 database.exec(kSchema);
 metadata["schema_version"] = kSchemaVersion;
 metadata["build_state"] = "building";
 metadata["build_phase"] = "relations";
 metadata["build_started_at"] = utc_now();
 metadata["builder"] = "libosmium-cpp-v1";
 set_metadata(database, metadata);

 osmium::area::Assembler::config_type assembler_config;
 osmium::TagsFilter area_filter{false};
 area_filter.add_rule(true, "name");
 area_filter.add_rule(true, "boundary", "administrative");
 for (const char* key : kAreaCategoryKeys) area_filter.add_rule(true, key);
 osmium::area::MultipolygonManager<osmium::area::Assembler> area_manager{
 assembler_config, std::move(area_filter)};
 const osmium::io::File input_file{arguments.source.string()};
 osmium::relations::read_relations(input_file, area_manager);

 const int location_fd = ::open(location_path.c_str(), O_CREAT | O_TRUNC | O_RDWR, 0644);
 if (location_fd < 0) throw std::system_error(errno, std::generic_category(), "open location index");
 using LocationIndex = osmium::index::map::SparseFileArray<
 osmium::unsigned_object_id_type, osmium::Location>;
 LocationIndex location_index{location_fd};
 osmium::handler::NodeLocationsForWays<LocationIndex> location_handler{location_index};
 location_handler.ignore_errors();
 ImportHandler import_handler{database, arguments.batch_size};
 auto area_handler = area_manager.handler([&import_handler](osmium::memory::Buffer&& buffer) {
 osmium::apply(buffer, import_handler);
 });
 set_metadata(database, {{"build_phase", "scan"}});
 osmium::io::Reader reader{input_file};
osmium::apply(reader, location_handler, import_handler, area_handler);
reader.close();
import_handler.finish();
 set_metadata(database, {{"bounds", import_handler.bounds_json()}});
set_metadata(database, {{"build_phase", "route_nodes"}});
 import_handler.write_route_nodes(location_index);
set_metadata(database, {{"build_phase", "indexes"}});
 build_indexes(database);
 set_metadata(database, {{"build_phase", "validation"}});
 const auto counts = validate(database);
 std::map<std::string, std::string> complete = counts;
 complete["build_state"] = "ready";
 complete["build_phase"] = "complete";
 complete["build_completed_at"] = utc_now();
 set_metadata(database, complete);
 database.exec("PRAGMA optimize");
 std::cout << '{';
 bool first = true;
 for (const auto& [name, count] : counts) {
 if (!first) std::cout << ',';
 first = false;
 std::cout << '"' << name << "\":" << count;
 }
 std::cout << "}\ndatabase=" << fs::absolute(arguments.output) << '\n' << std::flush;
 if (!arguments.keep_temp) fs::remove(location_path);
 return 0;
}

} // namespace

int main(int argc, char** argv) {
 try {
 return run(parse_arguments(argc, argv));
 } catch (const std::exception& error) {
 std::cerr << "error: " << error.what() << '\n';
 return 1;
 }
}
