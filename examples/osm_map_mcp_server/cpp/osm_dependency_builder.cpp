#include <osmium/handler.hpp>
#include <osmium/io/any_input.hpp>
#include <osmium/io/reader.hpp>
#include <osmium/visitor.hpp>

#include <sqlite3.h>

#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>

namespace fs = std::filesystem;

namespace {

class Database;

class Statement {
  sqlite3_stmt *statement_ = nullptr;
  Database *database_ = nullptr;

public:
  Statement(Database &database, const char *sql);
  ~Statement() { sqlite3_finalize(statement_); }
  void run();
  void integer(int index, std::int64_t value) {
    if (sqlite3_bind_int64(statement_, index, value) != SQLITE_OK)
      throw std::runtime_error("cannot bind SQLite integer");
  }
  void blob(int index, const std::string &value) {
    if (sqlite3_bind_blob(statement_, index, value.data(), static_cast<int>(value.size()),
                          SQLITE_TRANSIENT) != SQLITE_OK)
      throw std::runtime_error("cannot bind SQLite blob");
  }
  void text(int index, const char *value) {
    if (sqlite3_bind_text(statement_, index, value, -1, SQLITE_TRANSIENT) != SQLITE_OK)
      throw std::runtime_error("cannot bind SQLite text");
  }
};

class Database {
  sqlite3 *database_ = nullptr;

public:
  explicit Database(const fs::path &path) {
    if (sqlite3_open(path.c_str(), &database_) != SQLITE_OK)
      throw std::runtime_error("cannot create dependency database");
  }
  ~Database() {
    if (database_ != nullptr)
      sqlite3_close(database_);
  }
  sqlite3 *get() { return database_; }
  void exec(const std::string &sql) {
    char *error = nullptr;
    if (sqlite3_exec(database_, sql.c_str(), nullptr, nullptr, &error) != SQLITE_OK) {
      const std::string message = error == nullptr ? sqlite3_errmsg(database_) : error;
      sqlite3_free(error);
      throw std::runtime_error("SQLite error: " + message);
    }
  }
};

Statement::Statement(Database &database, const char *sql) : database_(&database) {
  if (sqlite3_prepare_v2(database.get(), sql, -1, &statement_, nullptr) != SQLITE_OK)
    throw std::runtime_error("cannot prepare dependency statement");
}

void Statement::run() {
  if (sqlite3_step(statement_) != SQLITE_DONE)
    throw std::runtime_error("dependency insert failed: " +
                             std::string(sqlite3_errmsg(database_->get())));
  sqlite3_reset(statement_);
  sqlite3_clear_bindings(statement_);
}

std::string encode_tags(const osmium::TagList &tags) {
  std::string output;
  auto append_size = [&output](std::uint32_t value) {
    for (int shift = 0; shift < 32; shift += 8)
      output.push_back(static_cast<char>((value >> shift) & 0xff));
  };
  for (const auto &tag : tags) {
    const auto key_size = static_cast<std::uint32_t>(std::strlen(tag.key()));
    const auto value_size = static_cast<std::uint32_t>(std::strlen(tag.value()));
    append_size(key_size);
    append_size(value_size);
    output.append(tag.key(), key_size);
    output.append(tag.value(), value_size);
  }
  return output;
}

class DependencyHandler : public osmium::handler::Handler {
  Database &database_;
  Statement node_;
  Statement way_;
  Statement way_node_;
  Statement relation_;
  Statement relation_member_;
  std::uint64_t pending_ = 0;
  std::uint64_t objects_ = 0;
  std::uint64_t nodes_ = 0;
  std::uint64_t ways_ = 0;
  std::uint64_t relations_ = 0;
  std::uint64_t way_nodes_ = 0;
  std::uint64_t relation_members_ = 0;
  std::chrono::steady_clock::time_point started_ = std::chrono::steady_clock::now();

  void touch() {
    if (++pending_ < 1000000)
      return;
    database_.exec("COMMIT;BEGIN");
    pending_ = 0;
  }

  void progress() {
    if (++objects_ % 1000000 != 0)
      return;
    const auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(
                             std::chrono::steady_clock::now() - started_)
                             .count();
    std::cout << "objects=" << objects_ << " way_nodes=" << way_nodes_
              << " relation_members=" << relation_members_ << " elapsed=" << elapsed << "s\n"
              << std::flush;
  }

public:
  explicit DependencyHandler(Database &database)
      : database_(database),
        node_(database, "INSERT INTO nodes(id,lat_e7,lon_e7,tags) VALUES(?,?,?,?)"),
        way_(database, "INSERT INTO ways(id,tags) VALUES(?,?)"),
        way_node_(database, "INSERT INTO way_nodes(way_id,seq,node_id) VALUES(?,?,?)"),
        relation_(database, "INSERT INTO relations(id,tags) VALUES(?,?)"),
        relation_member_(database,
                         "INSERT INTO relation_members(relation_id,seq,member_type,member_id,role) "
                         "VALUES(?,?,?,?,?)") {
    database_.exec("BEGIN");
  }

  void node(const osmium::Node &node) {
    progress();
    if (!node.location().valid())
      return;
    node_.integer(1, node.id());
    node_.integer(2, std::llround(node.location().lat() * 10000000.0));
    node_.integer(3, std::llround(node.location().lon() * 10000000.0));
    node_.blob(4, encode_tags(node.tags()));
    node_.run();
    ++nodes_;
    touch();
  }

  void way(const osmium::Way &way) {
    progress();
    way_.integer(1, way.id());
    way_.blob(2, encode_tags(way.tags()));
    way_.run();
    ++ways_;
    touch();
    std::int64_t sequence = 0;
    for (const auto &node : way.nodes()) {
      way_node_.integer(1, way.id());
      way_node_.integer(2, sequence++);
      way_node_.integer(3, node.ref());
      way_node_.run();
      ++way_nodes_;
      touch();
    }
  }

  void relation(const osmium::Relation &relation) {
    progress();
    relation_.integer(1, relation.id());
    relation_.blob(2, encode_tags(relation.tags()));
    relation_.run();
    ++relations_;
    touch();
    std::int64_t sequence = 0;
    for (const auto &member : relation.members()) {
      int type = -1;
      if (member.type() == osmium::item_type::node)
        type = 0;
      else if (member.type() == osmium::item_type::way)
        type = 1;
      else if (member.type() == osmium::item_type::relation)
        type = 2;
      if (type < 0)
        continue;
      relation_member_.integer(1, relation.id());
      relation_member_.integer(2, sequence++);
      relation_member_.integer(3, type);
      relation_member_.integer(4, member.ref());
      relation_member_.text(5, member.role());
      relation_member_.run();
      ++relation_members_;
      touch();
    }
  }

  void finish() {
    database_.exec("COMMIT");
    std::cout << "dependency rows: nodes=" << nodes_ << " ways=" << ways_
              << " relations=" << relations_ << " way_nodes=" << way_nodes_
              << " relation_members=" << relation_members_ << '\n'
              << std::flush;
  }

  std::uint64_t way_node_count() const { return way_nodes_; }
  std::uint64_t relation_member_count() const { return relation_members_; }
};

void build(const fs::path &source, const fs::path &output) {
  if (!fs::is_regular_file(source))
    throw std::runtime_error("source PBF does not exist");
  if (fs::exists(output))
    throw std::runtime_error("dependency database already exists");
  if (!output.parent_path().empty())
    fs::create_directories(output.parent_path());

  osmium::io::File input{source.string(), "pbf"};
  osmium::io::Reader header_reader{input, osmium::osm_entity_bits::nothing};
  const auto header = header_reader.header();
  header_reader.close();

  Database database{output};
  database.exec(
      "PRAGMA journal_mode=OFF;PRAGMA synchronous=OFF;PRAGMA temp_store=FILE;"
      "PRAGMA cache_size=-524288;PRAGMA locking_mode=EXCLUSIVE;"
      "CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
      "CREATE TABLE nodes(id INTEGER PRIMARY KEY,lat_e7 INTEGER NOT NULL,lon_e7 INTEGER NOT NULL,"
      "tags BLOB NOT NULL);"
      "CREATE TABLE ways(id INTEGER PRIMARY KEY,tags BLOB NOT NULL);"
      "CREATE TABLE way_nodes(way_id INTEGER NOT NULL,seq INTEGER NOT NULL,node_id INTEGER NOT "
      "NULL,"
      "PRIMARY KEY(way_id,seq)) WITHOUT ROWID;"
      "CREATE TABLE relations(id INTEGER PRIMARY KEY,tags BLOB NOT NULL);"
      "CREATE TABLE relation_members(relation_id INTEGER NOT NULL,seq INTEGER NOT NULL,"
      "member_type INTEGER NOT NULL,member_id INTEGER NOT NULL,role TEXT NOT NULL,"
      "PRIMARY KEY(relation_id,seq)) WITHOUT ROWID;");
  database.exec("INSERT INTO metadata VALUES('build_state','building')");

  DependencyHandler handler{database};
  osmium::io::Reader reader{input};
  osmium::apply(reader, handler);
  reader.close();
  handler.finish();

  std::cout << "index phase: way_nodes_node\n" << std::flush;
  database.exec("CREATE INDEX way_nodes_node ON way_nodes(node_id,way_id)");
  std::cout << "index phase: relation_members_child\n" << std::flush;
  database.exec("CREATE INDEX relation_members_child "
                "ON relation_members(member_type,member_id,relation_id)");
  database.exec("INSERT OR REPLACE INTO metadata VALUES('build_state','ready')");
  database.exec("INSERT OR REPLACE INTO metadata VALUES('schema_version','1')");
  database.exec("INSERT OR REPLACE INTO metadata VALUES('replication_sequence','" +
                header.get("osmosis_replication_sequence_number") + "')");
  database.exec("INSERT OR REPLACE INTO metadata VALUES('replication_timestamp','" +
                header.get("osmosis_replication_timestamp") + "')");
  database.exec("INSERT OR REPLACE INTO metadata VALUES('replication_base_url','" +
                header.get("osmosis_replication_base_url") + "')");
  database.exec("INSERT OR REPLACE INTO metadata VALUES('way_node_count','" +
                std::to_string(handler.way_node_count()) + "')");
  database.exec("INSERT OR REPLACE INTO metadata VALUES('relation_member_count','" +
                std::to_string(handler.relation_member_count()) + "')");
  database.exec("ANALYZE;PRAGMA optimize");
  std::cout << "dependency database=" << fs::absolute(output) << '\n' << std::flush;
}

} // namespace

int main(int argc, char **argv) {
  try {
    if (argc != 3)
      throw std::runtime_error("usage: osm_dependency_builder SOURCE.osm.pbf OUTPUT.sqlite");
    build(argv[1], argv[2]);
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "error: " << error.what() << '\n';
    return 1;
  }
}
