use std::path::PathBuf;
use std::time::Duration;

use clap::{ArgGroup, Parser, ValueEnum};
use knoa_update::{
    InstallConstraints, ReleaseKind, ReleaseRole, ReleaseStore, ReleaseTrustStore, TargetArch,
    TargetOs, TargetPlatform, extract_archive, run_health_check, verify_bundle,
};

#[derive(Clone, Debug, ValueEnum)]
enum KindArgument {
    Product,
    #[value(name = "runtime_extension")]
    RuntimeExtension,
}

#[derive(Clone, Debug, ValueEnum)]
enum RoleArgument {
    Hub,
    Node,
    All,
}

#[derive(Clone, Debug, ValueEnum)]
enum OsArgument {
    Windows,
    Linux,
}

#[derive(Clone, Debug, ValueEnum)]
enum ArchArgument {
    #[value(name = "x86_64")]
    X86_64,
    Aarch64,
}

#[derive(Debug, Parser)]
#[command(name = "knoa-update")]
#[command(group(ArgGroup::new("input").required(true).args(["bundle", "archive"])))]
struct Arguments {
    #[arg(long)]
    bundle: Option<PathBuf>,
    #[arg(long, requires = "staging")]
    archive: Option<PathBuf>,
    #[arg(long, requires = "archive")]
    staging: Option<PathBuf>,
    #[arg(long)]
    trust_store: PathBuf,
    #[arg(long, value_enum)]
    kind: KindArgument,
    #[arg(long, value_enum)]
    role: Option<RoleArgument>,
    #[arg(long, value_enum)]
    target_os: OsArgument,
    #[arg(long, value_enum)]
    target_arch: ArchArgument,
    #[arg(long, default_value_t = 1)]
    agent_runtime_spi: u32,
    #[arg(long, requires = "health_entrypoint")]
    install_root: Option<PathBuf>,
    #[arg(long, requires = "install_root")]
    health_entrypoint: Option<String>,
    #[arg(long, requires = "install_root")]
    health_arg: Vec<String>,
    #[arg(long, default_value_t = 60, requires = "install_root")]
    health_timeout_seconds: u64,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Arguments::parse();
    let constraints = InstallConstraints {
        release_kind: match args.kind {
            KindArgument::Product => ReleaseKind::Product,
            KindArgument::RuntimeExtension => ReleaseKind::RuntimeExtension,
        },
        role: args.role.map(|role| match role {
            RoleArgument::Hub => ReleaseRole::Hub,
            RoleArgument::Node => ReleaseRole::Node,
            RoleArgument::All => ReleaseRole::All,
        }),
        target: TargetPlatform {
            os: match args.target_os {
                OsArgument::Windows => TargetOs::Windows,
                OsArgument::Linux => TargetOs::Linux,
            },
            arch: match args.target_arch {
                ArchArgument::X86_64 => TargetArch::X86_64,
                ArchArgument::Aarch64 => TargetArch::Aarch64,
            },
        },
        agent_runtime_spi_version: args.agent_runtime_spi,
    };
    let trust_store = ReleaseTrustStore::load(&args.trust_store)?;
    let bundle = if let Some(archive) = args.archive {
        let staging = args.staging.ok_or("--archive requires --staging")?;
        extract_archive(&archive, &staging)?;
        staging
    } else {
        args.bundle.ok_or("--bundle or --archive is required")?
    };
    let release = verify_bundle(&bundle, &trust_store, &constraints)?;
    if let Some(install_root) = args.install_root {
        let health_entrypoint = args
            .health_entrypoint
            .ok_or("--install-root requires --health-entrypoint")?;
        let timeout = Duration::from_secs(args.health_timeout_seconds.clamp(1, 300));
        let store = ReleaseStore::new(install_root);
        let result = store.install(&bundle, &release, |candidate| {
            run_health_check(candidate, &health_entrypoint, &args.health_arg, timeout)
        })?;
        println!(
            "activated release={} previous={}",
            result.release_id, result.previous_release_id
        );
    } else {
        println!(
            "verified release={} version={}",
            release.manifest().release_id,
            release.manifest().version
        );
    }
    Ok(())
}
