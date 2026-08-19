use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;

use clap::{ArgGroup, Args, Parser, Subcommand, ValueEnum};
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

#[derive(Debug, Args)]
#[command(group(ArgGroup::new("input").required(true).args(["bundle", "archive"])))]
struct InstallArguments {
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
    #[arg(long)]
    install_root: PathBuf,
    #[arg(long)]
    health_entrypoint: String,
    #[arg(long)]
    health_arg: Vec<String>,
    #[arg(long, default_value_t = 60)]
    health_timeout_seconds: u64,
}

#[derive(Debug, Args)]
struct RollbackArguments {
    #[arg(long)]
    install_root: PathBuf,
    #[arg(long)]
    health_entrypoint: String,
    #[arg(long)]
    health_arg: Vec<String>,
    #[arg(long, default_value_t = 60)]
    health_timeout_seconds: u64,
}

#[derive(Debug, Args)]
struct RejectArguments {
    #[arg(long)]
    install_root: PathBuf,
    #[arg(long)]
    health_entrypoint: String,
    #[arg(long)]
    health_arg: Vec<String>,
    #[arg(long, default_value_t = 60)]
    health_timeout_seconds: u64,
}

#[derive(Debug, Args)]
struct CurrentArguments {
    #[arg(long)]
    install_root: PathBuf,
}

#[derive(Debug, Args)]
struct RunArguments {
    #[arg(long)]
    install_root: PathBuf,
    #[arg(long)]
    entrypoint: String,
    #[arg(last = true)]
    arguments: Vec<String>,
}

#[derive(Debug, Subcommand)]
enum UpdateCommand {
    Install(InstallArguments),
    Rollback(RollbackArguments),
    Reject(RejectArguments),
    Current(CurrentArguments),
    Run(RunArguments),
}

#[derive(Debug, Parser)]
#[command(name = "knoa-update")]
struct CommandLine {
    #[command(subcommand)]
    command: UpdateCommand,
}

fn health_timeout(seconds: u64) -> Duration {
    Duration::from_secs(seconds.clamp(1, 300))
}

fn install(args: InstallArguments) -> Result<(), Box<dyn std::error::Error>> {
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
    let timeout = health_timeout(args.health_timeout_seconds);
    let store = ReleaseStore::new(args.install_root);
    let result = store.install(&bundle, &release, |candidate| {
        run_health_check(
            candidate,
            &args.health_entrypoint,
            &args.health_arg,
            timeout,
        )
    })?;
    println!(
        "activated release={} previous={}",
        result.release_id, result.previous_release_id
    );
    Ok(())
}

fn rollback(args: RollbackArguments) -> Result<(), Box<dyn std::error::Error>> {
    let timeout = health_timeout(args.health_timeout_seconds);
    let store = ReleaseStore::new(args.install_root);
    let result = store.rollback(|candidate| {
        run_health_check(
            candidate,
            &args.health_entrypoint,
            &args.health_arg,
            timeout,
        )
    })?;
    println!(
        "rolled back release={} replaced={}",
        result.release_id, result.previous_release_id
    );
    Ok(())
}

fn reject(args: RejectArguments) -> Result<(), Box<dyn std::error::Error>> {
    let timeout = health_timeout(args.health_timeout_seconds);
    let store = ReleaseStore::new(args.install_root);
    let result = store.reject_current(|candidate| {
        run_health_check(
            candidate,
            &args.health_entrypoint,
            &args.health_arg,
            timeout,
        )
    })?;
    println!(
        "rejected release={} restored={}",
        result.previous_release_id, result.release_id
    );
    Ok(())
}

fn run(args: RunArguments) -> Result<(), Box<dyn std::error::Error>> {
    let store = ReleaseStore::new(args.install_root);
    let release_root = store
        .current_path()?
        .ok_or("no active Knoa Release is installed")?;
    let executable = store.current_entrypoint(&args.entrypoint)?;
    let mut command = Command::new(executable);
    command
        .args(args.arguments)
        .current_dir(&release_root)
        .env("KNOA_RELEASE_ROOT", &release_root);
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt as _;

        return Err(command.exec().into());
    }
    #[cfg(not(unix))]
    {
        let status = command.status()?;
        if !status.success() {
            return Err(format!("Knoa Release process exited with {status}").into());
        }
        Ok(())
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    match CommandLine::parse().command {
        UpdateCommand::Install(args) => install(args),
        UpdateCommand::Rollback(args) => rollback(args),
        UpdateCommand::Reject(args) => reject(args),
        UpdateCommand::Current(args) => {
            let store = ReleaseStore::new(args.install_root);
            let current = store
                .current_path()?
                .ok_or("no active Knoa Release is installed")?;
            println!("{}", current.display());
            Ok(())
        }
        UpdateCommand::Run(args) => run(args),
    }
}
