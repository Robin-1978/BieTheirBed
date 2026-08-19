use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::io::{BufReader, Read, Write};
use std::path::{Component, Path, PathBuf};
use std::process::Command;
use std::thread;
use std::time::{Duration, Instant};

use base64::Engine as _;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use thiserror::Error;
use walkdir::WalkDir;
use zip::ZipArchive;

pub const RELEASE_MANIFEST_FILE: &str = "release-manifest.json";
const MAX_ARCHIVE_FILES: usize = 20_000;
const MAX_ARCHIVE_FILE_BYTES: u64 = 2 * 1024 * 1024 * 1024;
const MAX_ARCHIVE_TOTAL_BYTES: u64 = 8 * 1024 * 1024 * 1024;

#[derive(Debug, Error)]
pub enum UpdateError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("invalid JSON: {0}")]
    Json(#[from] serde_json::Error),
    #[error("invalid Release Bundle: {0}")]
    InvalidBundle(String),
    #[error("Release signing key is not trusted")]
    UntrustedKey,
    #[error("Release signing key is not authorized for this release")]
    UnauthorizedKey,
    #[error("Release manifest signature is invalid")]
    InvalidSignature,
}

pub type Result<T> = std::result::Result<T, UpdateError>;

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReleaseKind {
    Product,
    RuntimeExtension,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReleaseRole {
    Hub,
    Node,
    All,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TargetOs {
    Windows,
    Linux,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TargetArch {
    X86_64,
    Aarch64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct TargetPlatform {
    pub os: TargetOs,
    pub arch: TargetArch,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ProtocolCompatibility {
    pub release_manifest: u32,
    pub agent_runtime_spi_min: u32,
    pub agent_runtime_spi_max: u32,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ArtifactKind {
    PythonRuntime,
    Application,
    Launcher,
    ConsoleAssets,
    RuntimeExtensionWorker,
    Metadata,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReleaseArtifact {
    pub path: String,
    pub kind: ArtifactKind,
    pub size: u64,
    pub sha256: String,
    pub executable: bool,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RuntimeExtensionDescriptor {
    pub extension_id: String,
    pub runtime_kind: String,
    pub display_name: String,
    pub publisher: String,
    pub entrypoint: Vec<String>,
    #[serde(default)]
    pub native_capability_ceiling: BTreeSet<String>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReleaseManifest {
    pub schema_version: u32,
    pub release_id: String,
    pub version: String,
    pub release_kind: ReleaseKind,
    pub role: Option<ReleaseRole>,
    pub target: TargetPlatform,
    pub created_at: String,
    pub protocols: ProtocolCompatibility,
    pub artifacts: Vec<ReleaseArtifact>,
    pub extension: Option<RuntimeExtensionDescriptor>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ReleaseSignature {
    pub algorithm: String,
    pub key_id: String,
    pub value: String,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct SignedReleaseManifest {
    pub manifest: ReleaseManifest,
    pub signature: ReleaseSignature,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReleaseTrustKey {
    pub key_id: String,
    pub public_key: String,
    pub allowed_release_kinds: BTreeSet<String>,
    #[serde(default)]
    pub allowed_extension_ids: BTreeSet<String>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReleaseTrustStore {
    pub schema_version: u32,
    pub keys: Vec<ReleaseTrustKey>,
}

#[derive(Clone, Debug)]
pub struct InstallConstraints {
    pub release_kind: ReleaseKind,
    pub role: Option<ReleaseRole>,
    pub target: TargetPlatform,
    pub agent_runtime_spi_version: u32,
}

#[derive(Clone, Debug)]
pub struct VerifiedRelease {
    signed: SignedReleaseManifest,
    manifest_bytes: Vec<u8>,
}

impl VerifiedRelease {
    pub fn manifest(&self) -> &ReleaseManifest {
        &self.signed.manifest
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ReleaseState {
    schema_version: u32,
    current: String,
    previous: String,
    failed: String,
}

impl Default for ReleaseState {
    fn default() -> Self {
        Self {
            schema_version: 1,
            current: String::new(),
            previous: String::new(),
            failed: String::new(),
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct UpdateResult {
    pub release_id: String,
    pub previous_release_id: String,
    pub rolled_back: bool,
}

pub struct ReleaseStore {
    root: PathBuf,
}

impl ReleaseTrustStore {
    pub fn load(path: &Path) -> Result<Self> {
        let store: Self = serde_json::from_reader(BufReader::new(File::open(path)?))?;
        if store.schema_version != 1 || store.keys.is_empty() {
            return Err(UpdateError::InvalidBundle(
                "unsupported or empty Release Trust Store".into(),
            ));
        }
        let mut ids = BTreeSet::new();
        if store.keys.iter().any(|key| !ids.insert(&key.key_id)) {
            return Err(UpdateError::InvalidBundle(
                "duplicate Release Trust Store key ID".into(),
            ));
        }
        Ok(store)
    }

    fn authorized_key(&self, key_id: &str, manifest: &ReleaseManifest) -> Result<VerifyingKey> {
        let key = self
            .keys
            .iter()
            .find(|candidate| candidate.key_id == key_id)
            .ok_or(UpdateError::UntrustedKey)?;
        let kind = match manifest.release_kind {
            ReleaseKind::Product => "product",
            ReleaseKind::RuntimeExtension => "runtime_extension",
        };
        if !key.allowed_release_kinds.contains(kind) {
            return Err(UpdateError::UnauthorizedKey);
        }
        if manifest.release_kind == ReleaseKind::RuntimeExtension {
            let extension_id = manifest
                .extension
                .as_ref()
                .map(|extension| extension.extension_id.as_str())
                .unwrap_or_default();
            if !key.allowed_extension_ids.contains("*")
                && !key.allowed_extension_ids.contains(extension_id)
            {
                return Err(UpdateError::UnauthorizedKey);
            }
        }
        let bytes = URL_SAFE_NO_PAD
            .decode(&key.public_key)
            .map_err(|_| UpdateError::InvalidBundle("invalid trust public key".into()))?;
        let raw: [u8; 32] = bytes
            .try_into()
            .map_err(|_| UpdateError::InvalidBundle("invalid trust public key length".into()))?;
        VerifyingKey::from_bytes(&raw)
            .map_err(|_| UpdateError::InvalidBundle("invalid Ed25519 public key".into()))
    }
}

pub fn verify_bundle(
    bundle_root: &Path,
    trust_store: &ReleaseTrustStore,
    constraints: &InstallConstraints,
) -> Result<VerifiedRelease> {
    let manifest_path = bundle_root.join(RELEASE_MANIFEST_FILE);
    let encoded = fs::read(&manifest_path)?;
    let raw: Value = serde_json::from_slice(&encoded)?;
    let signed: SignedReleaseManifest = serde_json::from_value(raw.clone())?;
    validate_manifest(&signed.manifest, constraints)?;
    if signed.signature.algorithm != "ed25519" {
        return Err(UpdateError::InvalidBundle(
            "unsupported Release signature algorithm".into(),
        ));
    }
    let public_key = trust_store.authorized_key(&signed.signature.key_id, &signed.manifest)?;
    let signature_bytes = URL_SAFE_NO_PAD
        .decode(&signed.signature.value)
        .map_err(|_| UpdateError::InvalidSignature)?;
    let signature =
        Signature::from_slice(&signature_bytes).map_err(|_| UpdateError::InvalidSignature)?;
    let manifest_value = raw
        .get("manifest")
        .ok_or_else(|| UpdateError::InvalidBundle("manifest object is missing".into()))?;
    let canonical = canonical_json(manifest_value)?;
    public_key
        .verify(&canonical, &signature)
        .map_err(|_| UpdateError::InvalidSignature)?;
    verify_inventory(bundle_root, &signed.manifest.artifacts)?;
    Ok(VerifiedRelease {
        signed,
        manifest_bytes: encoded,
    })
}

pub fn extract_archive(archive_path: &Path, destination: &Path) -> Result<()> {
    if destination.exists() && destination.read_dir()?.next().is_some() {
        return Err(UpdateError::InvalidBundle(
            "Release extraction destination must be empty".into(),
        ));
    }
    fs::create_dir_all(destination)?;
    let root = destination.canonicalize()?;
    let mut archive = ZipArchive::new(File::open(archive_path)?)
        .map_err(|error| UpdateError::InvalidBundle(error.to_string()))?;
    if archive.len() > MAX_ARCHIVE_FILES {
        return Err(UpdateError::InvalidBundle(
            "Release archive contains too many files".into(),
        ));
    }
    let mut names = BTreeSet::new();
    let mut total_size = 0_u64;
    for index in 0..archive.len() {
        let mut entry = archive
            .by_index(index)
            .map_err(|error| UpdateError::InvalidBundle(error.to_string()))?;
        let name = entry.name().to_owned();
        if !names.insert(name.clone()) {
            return Err(UpdateError::InvalidBundle(
                "Release archive contains duplicate paths".into(),
            ));
        }
        let relative = safe_relative_path(name.trim_end_matches('/'))?;
        if entry.is_dir() {
            fs::create_dir_all(root.join(relative))?;
            continue;
        }
        if entry
            .unix_mode()
            .is_some_and(|mode| mode & 0o170000 == 0o120000)
        {
            return Err(UpdateError::InvalidBundle(
                "Release archive cannot contain symlinks".into(),
            ));
        }
        let expected_size = entry.size();
        if expected_size > MAX_ARCHIVE_FILE_BYTES {
            return Err(UpdateError::InvalidBundle(
                "Release archive file exceeds size limit".into(),
            ));
        }
        total_size = total_size
            .checked_add(expected_size)
            .ok_or_else(|| UpdateError::InvalidBundle("archive size overflow".into()))?;
        if total_size > MAX_ARCHIVE_TOTAL_BYTES {
            return Err(UpdateError::InvalidBundle(
                "Release archive exceeds total size limit".into(),
            ));
        }
        let target = root.join(relative);
        let parent = target
            .parent()
            .ok_or_else(|| UpdateError::InvalidBundle("archive path has no parent".into()))?;
        fs::create_dir_all(parent)?;
        let canonical_parent = parent.canonicalize()?;
        if canonical_parent != root && !canonical_parent.starts_with(&root) {
            return Err(UpdateError::InvalidBundle(
                "Release archive path escapes destination".into(),
            ));
        }
        let mut output = File::options().write(true).create_new(true).open(&target)?;
        let copied = std::io::copy(&mut entry.by_ref().take(expected_size + 1), &mut output)?;
        output.flush()?;
        if copied != expected_size {
            return Err(UpdateError::InvalidBundle(
                "Release archive file size mismatch".into(),
            ));
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt as _;
            fs::set_permissions(&target, fs::Permissions::from_mode(0o600))?;
        }
    }
    Ok(())
}

fn validate_manifest(manifest: &ReleaseManifest, constraints: &InstallConstraints) -> Result<()> {
    if manifest.schema_version != 1 || manifest.protocols.release_manifest != 1 {
        return Err(UpdateError::InvalidBundle(
            "unsupported Release Manifest version".into(),
        ));
    }
    if manifest.release_kind != constraints.release_kind
        || manifest.role != constraints.role
        || manifest.target != constraints.target
    {
        return Err(UpdateError::InvalidBundle(
            "Release kind, role or target does not match installation".into(),
        ));
    }
    if constraints.agent_runtime_spi_version < manifest.protocols.agent_runtime_spi_min
        || constraints.agent_runtime_spi_version > manifest.protocols.agent_runtime_spi_max
    {
        return Err(UpdateError::InvalidBundle(
            "Agent Runtime SPI is incompatible".into(),
        ));
    }
    Ok(())
}

fn canonical_json(value: &Value) -> Result<Vec<u8>> {
    fn normalize(value: &Value) -> Value {
        match value {
            Value::Object(map) => {
                let sorted: BTreeMap<_, _> = map
                    .iter()
                    .map(|(key, value)| (key.clone(), normalize(value)))
                    .collect();
                serde_json::to_value(sorted).expect("BTreeMap serialization cannot fail")
            }
            Value::Array(items) => Value::Array(items.iter().map(normalize).collect()),
            other => other.clone(),
        }
    }
    Ok(serde_json::to_vec(&normalize(value))?)
}

fn safe_relative_path(value: &str) -> Result<PathBuf> {
    if value.is_empty() || value.contains('\\') || value.contains(':') {
        return Err(UpdateError::InvalidBundle("unsafe artifact path".into()));
    }
    let path = Path::new(value);
    if path.is_absolute()
        || path
            .components()
            .any(|part| !matches!(part, Component::Normal(_)))
    {
        return Err(UpdateError::InvalidBundle("unsafe artifact path".into()));
    }
    Ok(path.to_path_buf())
}

pub fn run_health_check(
    candidate_root: &Path,
    entrypoint: &str,
    arguments: &[String],
    timeout: Duration,
) -> Result<()> {
    let executable = candidate_root.join(safe_relative_path(entrypoint)?);
    let metadata = fs::symlink_metadata(&executable)?;
    if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
        return Err(UpdateError::InvalidBundle(
            "health entrypoint is not a regular file".into(),
        ));
    }
    let mut child = Command::new(&executable)
        .args(arguments)
        .current_dir(candidate_root)
        .env("KNOA_RELEASE_ROOT", candidate_root)
        .spawn()?;
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(status) = child.try_wait()? {
            if status.success() {
                return Ok(());
            }
            return Err(UpdateError::InvalidBundle(format!(
                "candidate health command failed with {status}"
            )));
        }
        if Instant::now() >= deadline {
            child.kill()?;
            let _ = child.wait();
            return Err(UpdateError::InvalidBundle(
                "candidate health command timed out".into(),
            ));
        }
        thread::sleep(Duration::from_millis(100));
    }
}

fn verify_inventory(bundle_root: &Path, artifacts: &[ReleaseArtifact]) -> Result<()> {
    let root = bundle_root.canonicalize()?;
    let declared: BTreeSet<_> = artifacts.iter().map(|item| item.path.clone()).collect();
    if declared.len() != artifacts.len() {
        return Err(UpdateError::InvalidBundle("duplicate artifact path".into()));
    }
    let mut actual = BTreeSet::new();
    for entry in WalkDir::new(&root).follow_links(false) {
        let entry = entry.map_err(|error| UpdateError::InvalidBundle(error.to_string()))?;
        if entry.path() == root {
            continue;
        }
        if entry.file_type().is_symlink() {
            return Err(UpdateError::InvalidBundle(
                "Release Bundle cannot contain symlinks".into(),
            ));
        }
        if entry.file_type().is_file() {
            let relative = entry
                .path()
                .strip_prefix(&root)
                .map_err(|_| UpdateError::InvalidBundle("artifact escaped root".into()))?;
            let portable = relative
                .components()
                .map(|component| component.as_os_str().to_string_lossy())
                .collect::<Vec<_>>()
                .join("/");
            if portable != RELEASE_MANIFEST_FILE {
                actual.insert(portable);
            }
        }
    }
    if actual != declared {
        return Err(UpdateError::InvalidBundle(
            "Release Bundle inventory does not match manifest".into(),
        ));
    }
    for artifact in artifacts {
        let relative = safe_relative_path(&artifact.path)?;
        let candidate = root.join(relative);
        let metadata = fs::symlink_metadata(&candidate)?;
        if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
            return Err(UpdateError::InvalidBundle(
                "artifact is not a regular file".into(),
            ));
        }
        let mut stream = BufReader::new(File::open(candidate)?);
        let mut digest = Sha256::new();
        let mut size = 0_u64;
        let mut buffer = [0_u8; 1024 * 1024];
        loop {
            let read = stream.read(&mut buffer)?;
            if read == 0 {
                break;
            }
            size += read as u64;
            digest.update(&buffer[..read]);
        }
        let actual_digest = format!("{:x}", digest.finalize());
        if size != artifact.size || actual_digest != artifact.sha256 {
            return Err(UpdateError::InvalidBundle(format!(
                "artifact digest mismatch: {}",
                artifact.path
            )));
        }
    }
    Ok(())
}

impl ReleaseStore {
    pub fn new(root: PathBuf) -> Self {
        Self { root }
    }

    pub fn install<F>(
        &self,
        bundle_root: &Path,
        release: &VerifiedRelease,
        health_check: F,
    ) -> Result<UpdateResult>
    where
        F: FnOnce(&Path) -> Result<()>,
    {
        fs::create_dir_all(self.versions_root())?;
        let release_id = &release.manifest().release_id;
        verify_inventory(bundle_root, &release.manifest().artifacts)?;
        if fs::read(bundle_root.join(RELEASE_MANIFEST_FILE))? != release.manifest_bytes {
            return Err(UpdateError::InvalidBundle(
                "Release Manifest changed after verification".into(),
            ));
        }
        let destination = self.versions_root().join(release_id);
        if destination.exists() {
            let existing = fs::read(destination.join(RELEASE_MANIFEST_FILE))?;
            let incoming = fs::read(bundle_root.join(RELEASE_MANIFEST_FILE))?;
            if existing != incoming || incoming != release.manifest_bytes {
                return Err(UpdateError::InvalidBundle(
                    "Release ID already exists with different content".into(),
                ));
            }
        } else {
            let staging = self.unique_staging_path(release_id)?;
            fs::create_dir(&staging)?;
            if let Err(error) = copy_bundle(bundle_root, &staging) {
                let _ = fs::remove_dir_all(&staging);
                return Err(error);
            }
            if fs::read(staging.join(RELEASE_MANIFEST_FILE))? != release.manifest_bytes {
                let _ = fs::remove_dir_all(&staging);
                return Err(UpdateError::InvalidBundle(
                    "staged Release Manifest changed during copy".into(),
                ));
            }
            if let Err(error) = verify_inventory(&staging, &release.manifest().artifacts) {
                let _ = fs::remove_dir_all(&staging);
                return Err(error);
            }
            if let Err(error) = apply_artifact_modes(&staging, release.manifest()) {
                let _ = fs::remove_dir_all(&staging);
                return Err(error);
            }
            if let Err(error) = fs::rename(&staging, &destination) {
                let _ = fs::remove_dir_all(&staging);
                return Err(error.into());
            }
        }

        let previous_state = self.read_state()?;
        if previous_state.current == *release_id {
            health_check(&destination)?;
            return Ok(UpdateResult {
                release_id: release_id.clone(),
                previous_release_id: previous_state.previous,
                rolled_back: false,
            });
        }
        self.write_state(&ReleaseState {
            schema_version: 1,
            current: release_id.clone(),
            previous: previous_state.current.clone(),
            failed: String::new(),
        })?;
        if let Err(error) = health_check(&destination) {
            self.write_state(&ReleaseState {
                schema_version: 1,
                current: previous_state.current,
                previous: previous_state.previous,
                failed: release_id.clone(),
            })?;
            return Err(error);
        }
        Ok(UpdateResult {
            release_id: release_id.clone(),
            previous_release_id: previous_state.current,
            rolled_back: false,
        })
    }

    pub fn rollback<F>(&self, health_check: F) -> Result<UpdateResult>
    where
        F: FnOnce(&Path) -> Result<()>,
    {
        let state = self.read_state()?;
        if state.current.is_empty() || state.previous.is_empty() {
            return Err(UpdateError::InvalidBundle(
                "no previous Release is available".into(),
            ));
        }
        let candidate = self.versions_root().join(&state.previous);
        if !candidate.is_dir() {
            return Err(UpdateError::InvalidBundle(
                "previous Release directory is missing".into(),
            ));
        }
        health_check(&candidate)?;
        self.write_state(&ReleaseState {
            schema_version: 1,
            current: state.previous.clone(),
            previous: state.current.clone(),
            failed: String::new(),
        })?;
        Ok(UpdateResult {
            release_id: state.previous,
            previous_release_id: state.current,
            rolled_back: true,
        })
    }

    pub fn reject_current<F>(&self, health_check: F) -> Result<UpdateResult>
    where
        F: FnOnce(&Path) -> Result<()>,
    {
        let state = self.read_state()?;
        if state.current.is_empty() {
            return Err(UpdateError::InvalidBundle(
                "no active Release is available to reject".into(),
            ));
        }
        let rejected = state.current;
        if state.previous.is_empty() {
            self.write_state(&ReleaseState {
                schema_version: 1,
                current: String::new(),
                previous: String::new(),
                failed: rejected.clone(),
            })?;
            return Ok(UpdateResult {
                release_id: String::new(),
                previous_release_id: rejected,
                rolled_back: true,
            });
        }
        let candidate = self.versions_root().join(&state.previous);
        if !candidate.is_dir() {
            return Err(UpdateError::InvalidBundle(
                "previous Release directory is missing".into(),
            ));
        }
        health_check(&candidate)?;
        self.write_state(&ReleaseState {
            schema_version: 1,
            current: state.previous.clone(),
            previous: rejected.clone(),
            failed: rejected.clone(),
        })?;
        Ok(UpdateResult {
            release_id: state.previous,
            previous_release_id: rejected,
            rolled_back: true,
        })
    }

    pub fn current_path(&self) -> Result<Option<PathBuf>> {
        let state = self.read_state()?;
        Ok((!state.current.is_empty()).then(|| self.versions_root().join(state.current)))
    }

    pub fn current_entrypoint(&self, entrypoint: &str) -> Result<PathBuf> {
        let root = self
            .current_path()?
            .ok_or_else(|| UpdateError::InvalidBundle("no active Release is installed".into()))?;
        let executable = root.join(safe_relative_path(entrypoint)?);
        let metadata = fs::symlink_metadata(&executable)?;
        if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
            return Err(UpdateError::InvalidBundle(
                "Release entrypoint is not a regular file".into(),
            ));
        }
        Ok(executable)
    }

    fn versions_root(&self) -> PathBuf {
        self.root.join("versions")
    }

    fn state_path(&self) -> PathBuf {
        self.root.join("state.json")
    }

    fn read_state(&self) -> Result<ReleaseState> {
        let path = self.state_path();
        if !path.is_file() {
            return Ok(ReleaseState::default());
        }
        let state: ReleaseState = serde_json::from_reader(BufReader::new(File::open(path)?))?;
        if state.schema_version != 1 {
            return Err(UpdateError::InvalidBundle(
                "unsupported Release Store state".into(),
            ));
        }
        Ok(state)
    }

    fn write_state(&self, state: &ReleaseState) -> Result<()> {
        fs::create_dir_all(&self.root)?;
        let temporary = self.root.join(format!(".state.{}.tmp", std::process::id()));
        let mut output = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)?;
        serde_json::to_writer_pretty(&mut output, state)?;
        output.write_all(b"\n")?;
        output.sync_all()?;
        atomic_replace(&temporary, &self.state_path())?;
        Ok(())
    }

    fn unique_staging_path(&self, release_id: &str) -> Result<PathBuf> {
        for attempt in 0..1000_u32 {
            let path = self.versions_root().join(format!(
                ".{release_id}.{}.{}",
                std::process::id(),
                attempt
            ));
            if !path.exists() {
                return Ok(path);
            }
        }
        Err(UpdateError::InvalidBundle(
            "could not allocate Release staging directory".into(),
        ))
    }
}

fn copy_bundle(source: &Path, destination: &Path) -> Result<()> {
    for entry in WalkDir::new(source).min_depth(1).follow_links(false) {
        let entry = entry.map_err(|error| UpdateError::InvalidBundle(error.to_string()))?;
        if entry.file_type().is_symlink() {
            return Err(UpdateError::InvalidBundle(
                "Release Bundle cannot contain symlinks".into(),
            ));
        }
        let relative = entry
            .path()
            .strip_prefix(source)
            .map_err(|_| UpdateError::InvalidBundle("Bundle path escaped root".into()))?;
        let target = destination.join(relative);
        if entry.file_type().is_dir() {
            fs::create_dir_all(target)?;
        } else if entry.file_type().is_file() {
            if let Some(parent) = target.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::copy(entry.path(), target)?;
        } else {
            return Err(UpdateError::InvalidBundle(
                "Release Bundle contains a non-file entry".into(),
            ));
        }
    }
    Ok(())
}

fn apply_artifact_modes(root: &Path, manifest: &ReleaseManifest) -> Result<()> {
    if manifest.target.os != TargetOs::Linux {
        return Ok(());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt as _;

        for artifact in &manifest.artifacts {
            let target = root.join(safe_relative_path(&artifact.path)?);
            fs::set_permissions(
                target,
                fs::Permissions::from_mode(if artifact.executable { 0o755 } else { 0o644 }),
            )?;
        }
    }
    Ok(())
}

#[cfg(unix)]
fn atomic_replace(source: &Path, destination: &Path) -> Result<()> {
    fs::rename(source, destination)?;
    Ok(())
}

#[cfg(windows)]
fn atomic_replace(source: &Path, destination: &Path) -> Result<()> {
    use std::os::windows::ffi::OsStrExt as _;
    use windows_sys::Win32::Storage::FileSystem::{
        MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH, MoveFileExW,
    };

    let source_wide: Vec<u16> = source.as_os_str().encode_wide().chain(Some(0)).collect();
    let destination_wide: Vec<u16> = destination
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect();
    let result = unsafe {
        MoveFileExW(
            source_wide.as_ptr(),
            destination_wide.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if result == 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use zip::ZipWriter;
    use zip::write::SimpleFileOptions;

    fn repository_root() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .canonicalize()
            .unwrap()
    }

    #[test]
    fn canonical_json_sorts_nested_objects() {
        let value: Value = serde_json::json!({"z": 1, "a": {"y": 2, "b": 3}});
        assert_eq!(
            canonical_json(&value).unwrap(),
            br#"{"a":{"b":3,"y":2},"z":1}"#
        );
    }

    #[test]
    fn safe_paths_reject_parent_and_windows_forms() {
        assert!(safe_relative_path("app/knoa.whl").is_ok());
        assert!(safe_relative_path("../outside").is_err());
        assert!(safe_relative_path("C:/outside").is_err());
        assert!(safe_relative_path("app\\outside").is_err());
    }

    #[test]
    fn verifies_python_signed_release_fixture() {
        let root = repository_root().join("protocol/fixtures/release-v1");
        let trust = ReleaseTrustStore::load(&root.join("trust-store.json")).unwrap();
        let release = verify_bundle(
            &root.join("product-node-linux"),
            &trust,
            &InstallConstraints {
                release_kind: ReleaseKind::Product,
                role: Some(ReleaseRole::Node),
                target: TargetPlatform {
                    os: TargetOs::Linux,
                    arch: TargetArch::X86_64,
                },
                agent_runtime_spi_version: 1,
            },
        )
        .unwrap();
        assert_eq!(release.manifest().release_id, "fixture-node-linux-1.0.0");
    }

    #[test]
    fn native_archive_extraction_rejects_path_traversal() {
        let temporary = tempfile::tempdir().unwrap();
        let archive_path = temporary.path().join("malicious.zip");
        let mut writer = ZipWriter::new(File::create(&archive_path).unwrap());
        writer
            .start_file("../outside", SimpleFileOptions::default())
            .unwrap();
        writer.write_all(b"owned").unwrap();
        writer.finish().unwrap();

        let error = extract_archive(&archive_path, &temporary.path().join("output"))
            .expect_err("path traversal must fail");
        assert!(error.to_string().contains("unsafe artifact path"));
        assert!(!temporary.path().join("outside").exists());
    }

    #[test]
    fn native_archive_extraction_round_trips_regular_files() {
        let temporary = tempfile::tempdir().unwrap();
        let archive_path = temporary.path().join("bundle.zip");
        let mut writer = ZipWriter::new(File::create(&archive_path).unwrap());
        writer
            .start_file("app/knoa.whl", SimpleFileOptions::default())
            .unwrap();
        writer.write_all(b"application").unwrap();
        writer.finish().unwrap();

        let output = temporary.path().join("output");
        extract_archive(&archive_path, &output).unwrap();
        assert_eq!(
            fs::read(output.join("app/knoa.whl")).unwrap(),
            b"application"
        );
    }

    #[test]
    fn native_release_store_activates_verified_release() {
        let fixture = repository_root().join("protocol/fixtures/release-v1");
        let bundle = fixture.join("product-node-linux");
        let trust = ReleaseTrustStore::load(&fixture.join("trust-store.json")).unwrap();
        let release = verify_bundle(
            &bundle,
            &trust,
            &InstallConstraints {
                release_kind: ReleaseKind::Product,
                role: Some(ReleaseRole::Node),
                target: TargetPlatform {
                    os: TargetOs::Linux,
                    arch: TargetArch::X86_64,
                },
                agent_runtime_spi_version: 1,
            },
        )
        .unwrap();
        let temporary = tempfile::tempdir().unwrap();
        let store = ReleaseStore::new(temporary.path().join("store"));

        let result = store
            .install(&bundle, &release, |candidate| {
                if candidate.join("bin/knoa").is_file() {
                    Ok(())
                } else {
                    Err(UpdateError::InvalidBundle("launcher missing".into()))
                }
            })
            .unwrap();

        assert_eq!(result.release_id, "fixture-node-linux-1.0.0");
        assert_eq!(
            store.current_path().unwrap().unwrap(),
            temporary
                .path()
                .join("store/versions/fixture-node-linux-1.0.0")
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt as _;
            let mode = fs::metadata(
                temporary
                    .path()
                    .join("store/versions/fixture-node-linux-1.0.0/bin/knoa"),
            )
            .unwrap()
            .permissions()
            .mode();
            assert_ne!(mode & 0o100, 0);
        }
    }

    #[test]
    fn native_release_store_restores_empty_pointer_after_failed_health() {
        let fixture = repository_root().join("protocol/fixtures/release-v1");
        let bundle = fixture.join("product-node-linux");
        let trust = ReleaseTrustStore::load(&fixture.join("trust-store.json")).unwrap();
        let release = verify_bundle(
            &bundle,
            &trust,
            &InstallConstraints {
                release_kind: ReleaseKind::Product,
                role: Some(ReleaseRole::Node),
                target: TargetPlatform {
                    os: TargetOs::Linux,
                    arch: TargetArch::X86_64,
                },
                agent_runtime_spi_version: 1,
            },
        )
        .unwrap();
        let temporary = tempfile::tempdir().unwrap();
        let store = ReleaseStore::new(temporary.path().join("store"));

        let error = store
            .install(&bundle, &release, |_candidate| {
                Err(UpdateError::InvalidBundle("unhealthy".into()))
            })
            .expect_err("health failure must fail activation");

        assert!(error.to_string().contains("unhealthy"));
        assert!(store.current_path().unwrap().is_none());
    }

    #[test]
    fn native_release_store_rolls_back_and_resolves_current_entrypoint() {
        let fixture = repository_root().join("protocol/fixtures/release-v1");
        let bundle = fixture.join("product-node-linux");
        let trust = ReleaseTrustStore::load(&fixture.join("trust-store.json")).unwrap();
        let release = verify_bundle(
            &bundle,
            &trust,
            &InstallConstraints {
                release_kind: ReleaseKind::Product,
                role: Some(ReleaseRole::Node),
                target: TargetPlatform {
                    os: TargetOs::Linux,
                    arch: TargetArch::X86_64,
                },
                agent_runtime_spi_version: 1,
            },
        )
        .unwrap();
        let temporary = tempfile::tempdir().unwrap();
        let store = ReleaseStore::new(temporary.path().join("store"));
        store.install(&bundle, &release, |_| Ok(())).unwrap();

        let state = ReleaseState {
            schema_version: 1,
            current: "candidate".into(),
            previous: release.manifest().release_id.clone(),
            failed: String::new(),
        };
        fs::create_dir_all(temporary.path().join("store/versions/candidate")).unwrap();
        store.write_state(&state).unwrap();
        let result = store
            .rollback(|candidate| {
                assert!(candidate.join("bin/knoa").is_file());
                Ok(())
            })
            .unwrap();

        assert!(result.rolled_back);
        assert_eq!(
            store.current_entrypoint("bin/knoa").unwrap(),
            temporary
                .path()
                .join("store/versions/fixture-node-linux-1.0.0/bin/knoa")
        );
        assert!(store.current_entrypoint("../outside").is_err());
    }

    #[test]
    fn native_release_store_rejects_unhealthy_first_release() {
        let temporary = tempfile::tempdir().unwrap();
        let store = ReleaseStore::new(temporary.path().join("store"));
        store
            .write_state(&ReleaseState {
                schema_version: 1,
                current: "bad-first-release".into(),
                previous: String::new(),
                failed: String::new(),
            })
            .unwrap();

        let result = store
            .reject_current(|_| panic!("no previous health check"))
            .unwrap();

        assert!(result.rolled_back);
        assert!(result.release_id.is_empty());
        assert!(store.current_path().unwrap().is_none());
        assert_eq!(store.read_state().unwrap().failed, "bad-first-release");
    }
}
