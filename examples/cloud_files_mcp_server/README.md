# cloud_files MCP reference server

Independent MCP package for cloud evidence files. Migrated out of the Jira
reference server: OSS buckets and Tempo shared robot records are not Jira
APIs and only borrowed `issue_key` as a directory name.

## Tools

| Tool | Description |
|------|-------------|
| `cloud_files.list_files` | List OSS prefix objects or Tempo shared records (`source: oss\|tempo`) |
| `cloud_files.download_file` | Download one file into a caller `case_id` directory (host approval) |

The transport layers (`oss_files.py`, `tempo_files.py`) are copies of the Jira
package's downloaders so this package stays independently deployable. Jira
issue-text link extraction stays in the Jira package.

## Run

```bash
CLOUD_FILES_DOWNLOAD_ROOT=/tmp/cloud_files python3 server.py
```
