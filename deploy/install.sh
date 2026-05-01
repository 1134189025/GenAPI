#!/usr/bin/env bash
set -Eeuo pipefail

DEFAULT_REPO="1134189025/GenAPI"
INSTALL_DIR="/opt/genapi"
APP_DIR="/opt/genapi/app"
RELEASES_DIR="/opt/genapi/app/releases"
DATA_DIR="/opt/genapi/data"
BACKUP_DIR="/var/backups/genapi"
SERVICE_PATH="/etc/systemd/system/genapi.service"

REPO="${GENAPI_REPO:-${DEFAULT_REPO}}"
VERSION="${GENAPI_VERSION:-}"
TMP_DIR=""

usage() {
  cat <<'USAGE'
Usage:
  install.sh install [--version 0.1.10] [--repo owner/repo]
  install.sh upgrade [--version 0.1.10] [--repo owner/repo]
  install.sh rollback

Environment:
  GENAPI_REPO           GitHub repository, default: 1134189025/GenAPI
  GENAPI_VERSION        Version or tag to install, for example 0.1.10 or v0.1.10
  GENAPI_GITHUB_TOKEN   Optional token for private release downloads
USAGE
}

die() {
  echo "error: $*" >&2
  exit 1
}

log() {
  echo "==> $*" >&2
}

cleanup() {
  if [ -n "${TMP_DIR}" ] && [ -d "${TMP_DIR}" ]; then
    rm -rf "${TMP_DIR}"
  fi
}
trap cleanup EXIT

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    die "run this script as root"
  fi
}

need_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

curl_auth_args() {
  if [ -n "${GENAPI_GITHUB_TOKEN:-}" ]; then
    printf '%s\n' "-H"
    printf '%s\n' "Authorization: Bearer ${GENAPI_GITHUB_TOKEN}"
  fi
}

github_curl() {
  local output="$1"
  local url="$2"
  shift 2
  local args=()
  while IFS= read -r item; do
    args+=("${item}")
  done < <(curl_auth_args)
  curl -fL --retry 3 --retry-delay 2 "${args[@]}" -o "${output}" "$@" "${url}"
}

github_curl_stdout() {
  local url="$1"
  local args=()
  while IFS= read -r item; do
    args+=("${item}")
  done < <(curl_auth_args)
  curl -fsSL "${args[@]}" "${url}"
}

normalize_version() {
  local value="$1"
  value="${value#v}"
  [ -n "${value}" ] || die "version is empty"
  printf '%s\n' "${value}"
}

latest_version() {
  local tag
  tag="$(
    github_curl_stdout "https://api.github.com/repos/${REPO}/releases/latest" \
      | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
      | head -n 1
  )"
  [ -n "${tag}" ] || die "could not determine latest release tag for ${REPO}"
  normalize_version "${tag}"
}

detect_arch() {
  local machine="${GENAPI_ARCH:-$(uname -m)}"
  case "${machine}" in
    amd64 | x86_64)
      printf '%s\n' "amd64"
      ;;
    arm64 | aarch64)
      printf '%s\n' "arm64"
      ;;
    *)
      die "unsupported architecture: ${machine}"
      ;;
  esac
}

nologin_shell() {
  if [ -x /usr/sbin/nologin ]; then
    printf '%s\n' /usr/sbin/nologin
  else
    printf '%s\n' /bin/false
  fi
}

ensure_user() {
  if ! getent group genapi >/dev/null 2>&1; then
    groupadd --system genapi
  fi
  if ! id -u genapi >/dev/null 2>&1; then
    useradd --system --gid genapi --home-dir "${INSTALL_DIR}" --shell "$(nologin_shell)" genapi
  fi
}

write_service() {
  cat >"${SERVICE_PATH}" <<SERVICE
[Unit]
Description=Genapi
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=genapi
Group=genapi
WorkingDirectory=/opt/genapi/app/current
ExecStart=/opt/genapi/app/current/genapi
Restart=always
RestartSec=5
ReadWritePaths=/opt/genapi/app /opt/genapi/data
Environment=GENAPI_DEPLOYMENT_MODE=systemd-binary
Environment=GENAPI_BUILD_TYPE=release
Environment=GENAPI_DATA_DIR=/opt/genapi/data
Environment=GENAPI_HOST=0.0.0.0
Environment=GENAPI_PORT=3000
Environment=GENAPI_UPDATE_REPO=${REPO}

[Install]
WantedBy=multi-user.target
SERVICE
}

prepare_dirs() {
  ensure_directory "${INSTALL_DIR}" root root 0755
  ensure_directory "${APP_DIR}" root genapi 0775
  ensure_directory "${RELEASES_DIR}" root genapi 0775
  ensure_directory "${DATA_DIR}" genapi genapi 0750
  ensure_directory "${BACKUP_DIR}" root root 0700
}

ensure_directory() {
  local path="$1"
  local owner="$2"
  local group="$3"
  local mode="$4"
  if [ -L "${path}" ]; then
    die "refusing to use symlinked directory: ${path}"
  fi
  if [ -e "${path}" ] && [ ! -d "${path}" ]; then
    die "path exists but is not a directory: ${path}"
  fi
  mkdir -p "${path}"
  if [ -L "${path}" ] || [ ! -d "${path}" ]; then
    die "refusing to use unsafe directory: ${path}"
  fi
  chown "${owner}:${group}" "${path}"
  chmod "${mode}" "${path}"
}

stop_service_if_present() {
  systemctl stop genapi >/dev/null 2>&1 || true
}

validate_live_tree() {
  local release_dir="$1"
  case "${release_dir}" in
    "${RELEASES_DIR}"/*) ;;
    *) die "current release points outside releases directory: ${release_dir}" ;;
  esac
  validate_payload_tree "${release_dir}"
}

reset_runtime_permissions() {
  chown root:root "${INSTALL_DIR}"
  chmod 0755 "${INSTALL_DIR}"
  chown root:genapi "${APP_DIR}" "${RELEASES_DIR}"
  chmod 0775 "${APP_DIR}" "${RELEASES_DIR}"
  chown genapi:genapi "${DATA_DIR}"
  chmod 0750 "${DATA_DIR}"
  chown root:root "${BACKUP_DIR}"
  chmod 0700 "${BACKUP_DIR}"
}

current_release_dir() {
  local current="${APP_DIR}/current"
  if [ ! -L "${current}" ]; then
    return 1
  fi
  readlink -f "${current}"
}

backup_current() {
  local current
  if ! current="$(current_release_dir)"; then
    return 0
  fi
  validate_live_tree "${current}"
  local backup
  local backup_payload
  backup="$(mktemp "${BACKUP_DIR}/genapi-backup-$(date -u +%Y%m%d%H%M%S)-XXXXXX.tar.gz")"
  backup_payload="${TMP_DIR}/rollback-payload"
  rm -rf "${backup_payload}"
  mkdir -p "${backup_payload}"
  cp "${current}/genapi" "${backup_payload}/genapi"
  cp "${current}/VERSION" "${backup_payload}/VERSION"
  cp -R "${current}/web_dist" "${backup_payload}/web_dist"
  validate_payload_tree "${backup_payload}"
  log "Creating rollback backup ${backup}"
  tar -C "${backup_payload}" -czf "${backup}" genapi VERSION web_dist
}

clear_legacy_runtime_files() {
  find -P "${INSTALL_DIR}" -mindepth 1 -maxdepth 1 \
    ! -name data \
    ! -name app \
    -exec rm -rf -- {} +
}

validate_archive() {
  local archive="$1"
  local entry
  local metadata
  local type
  while IFS= read -r metadata; do
    [ -n "${metadata}" ] || continue
    type="${metadata:0:1}"
    case "${type}" in
      - | d) ;;
      *) die "archive contains symlinks or special files" ;;
    esac
  done < <(tar -tzvf "${archive}")
  while IFS= read -r entry; do
    case "${entry}" in
      "" | /* | *../* | ../* | */.. | *//*) die "unsafe archive entry: ${entry}" ;;
    esac
    if [ "${entry}" = "." ] || [ "${entry}" = ".." ]; then
      die "unsafe archive entry: ${entry}"
    fi
  done < <(tar -tzf "${archive}")
}

safe_extract_archive() {
  local archive="$1"
  local target_dir="$2"
  validate_archive "${archive}"
  tar --no-same-owner --no-same-permissions -xzf "${archive}" -C "${target_dir}"
}

validate_payload_tree() {
  local payload_dir="$1"
  [ -d "${payload_dir}" ] || die "archive payload directory is missing"
  if find -P "${payload_dir}" \( -type l -o -type b -o -type c -o -type p -o -type s \) -print -quit | grep -q .; then
    die "archive contains symlinks or special files"
  fi
  [ -f "${payload_dir}/genapi" ] || die "archive does not contain regular executable genapi"
  [ -f "${payload_dir}/VERSION" ] || die "archive does not contain regular VERSION"
  [ -d "${payload_dir}/web_dist" ] || die "archive does not contain web_dist"
}

copy_payload_tree() {
  local payload_dir="$1"
  local release_dir="$2"
  validate_payload_tree "${payload_dir}"
  [ -d "${release_dir}" ] || die "release directory is missing: ${release_dir}"
  cp "${payload_dir}/genapi" "${release_dir}/genapi"
  cp "${payload_dir}/VERSION" "${release_dir}/VERSION"
  cp -R "${payload_dir}/web_dist" "${release_dir}/web_dist"
  chmod 0755 "${release_dir}/genapi"
  chmod u-s,g-s "${release_dir}/genapi"
  chmod -R u=rwX,g=rX,o= "${release_dir}"
  chown -R genapi:genapi "${release_dir}"
  validate_payload_tree "${release_dir}"
}

create_release_from_payload() {
  local payload_dir="$1"
  local version="$2"
  local release_dir
  release_dir="$(mktemp -d "${RELEASES_DIR}/release-${version}-XXXXXX")"
  chmod 0700 "${release_dir}"
  copy_payload_tree "${payload_dir}" "${release_dir}"
  printf '%s\n' "${release_dir}"
}

activate_release() {
  local release_dir="$1"
  local current_link="${APP_DIR}/current"
  local previous_link="${APP_DIR}/previous"
  local current_tmp="${APP_DIR}/.current.$$"
  local previous_tmp="${APP_DIR}/.previous.$$"
  local old_release=""

  validate_live_tree "${release_dir}"
  if old_release="$(current_release_dir)"; then
    validate_live_tree "${old_release}"
    ln -sfn "${old_release}" "${previous_tmp}"
    mv -Tf "${previous_tmp}" "${previous_link}"
  fi
  ln -sfn "${release_dir}" "${current_tmp}"
  mv -Tf "${current_tmp}" "${current_link}"
  reset_runtime_permissions
}

download_release() {
  local version="$1"
  local arch="$2"
  local tag="v${version}"
  local asset="genapi_${version}_linux_${arch}.tar.gz"
  local base_url="https://github.com/${REPO}/releases/download/${tag}"
  local archive="${TMP_DIR}/${asset}"
  local checksums="${TMP_DIR}/checksums.txt"

  log "Downloading ${asset} from ${REPO}"
  github_curl "${archive}" "${base_url}/${asset}"

  github_curl "${checksums}" "${base_url}/checksums.txt" --silent --show-error || die "checksums.txt is required for ${asset}"

  local expected
  expected="$(grep -E "(^|[[:space:]])${asset}$" "${checksums}" | awk '{print $1}' | head -n 1)"
  [ -n "${expected}" ] || die "checksums.txt does not list ${asset}"
  log "Verifying checksum for ${asset}"
  (cd "${TMP_DIR}" && printf '%s  %s\n' "${expected}" "${asset}" | sha256sum -c -) >&2

  printf '%s\n' "${archive}"
}

install_payload() {
  local archive="$1"
  local version="$2"
  local arch="$3"
  local payload_dir="${TMP_DIR}/genapi_${version}_linux_${arch}"
  local release_dir

  safe_extract_archive "${archive}" "${TMP_DIR}"
  validate_payload_tree "${payload_dir}"
  [ -x "${payload_dir}/genapi" ] || die "archive does not contain executable genapi"

  stop_service_if_present
  backup_current
  clear_legacy_runtime_files
  release_dir="$(create_release_from_payload "${payload_dir}" "${version}")"
  activate_release "${release_dir}"
}

restart_service() {
  systemctl daemon-reload
  systemctl enable genapi
  systemctl restart genapi
}

install_or_upgrade() {
  local action="$1"
  local version="${VERSION}"
  local arch
  local archive

  require_root
  need_command curl
  need_command tar
  need_command sha256sum
  need_command systemctl

  ensure_user
  prepare_dirs

  if [ -z "${version}" ] || [ "${version}" = "latest" ]; then
    version="$(latest_version)"
  else
    version="$(normalize_version "${version}")"
  fi
  arch="$(detect_arch)"

  TMP_DIR="$(mktemp -d)"
  archive="$(download_release "${version}" "${arch}")"
  log "${action} Genapi ${version} (${arch}) into ${INSTALL_DIR}"
  install_payload "${archive}" "${version}" "${arch}"
  write_service
  restart_service
  log "Genapi ${version} is running under systemd"
}

rollback() {
  require_root
  need_command tar
  need_command systemctl
  ensure_user
  prepare_dirs
  write_service

  local backup
  local restore_dir
  local release_dir
  backup="$(find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'genapi-backup-*.tar.gz' | sort | tail -n 1)"
  [ -n "${backup}" ] || die "no rollback backup found in ${BACKUP_DIR}"

  TMP_DIR="$(mktemp -d)"
  restore_dir="${TMP_DIR}/rollback"
  mkdir -p "${restore_dir}"
  log "Rolling back from ${backup}"
  safe_extract_archive "${backup}" "${restore_dir}"
  validate_payload_tree "${restore_dir}"
  systemctl stop genapi >/dev/null 2>&1 || true
  release_dir="$(create_release_from_payload "${restore_dir}" "rollback-$(date -u +%Y%m%d%H%M%S)")"
  activate_release "${release_dir}"
  write_service
  restart_service
  log "Rollback complete"
}

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --version)
        [ "$#" -ge 2 ] || die "--version requires a value"
        VERSION="$2"
        shift 2
        ;;
      --repo)
        [ "$#" -ge 2 ] || die "--repo requires a value"
        REPO="$2"
        shift 2
        ;;
      -h | --help)
        usage
        exit 0
        ;;
      *)
        die "unknown argument: $1"
        ;;
    esac
  done
}

COMMAND="${1:-install}"
if [ "$#" -gt 0 ]; then
  shift
fi
parse_args "$@"

[[ "${REPO}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "invalid GitHub repository: ${REPO}"

if [ "${GENAPI_INSTALL_SH_LIBRARY:-0}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

case "${COMMAND}" in
  install)
    install_or_upgrade "Installing"
    ;;
  upgrade)
    install_or_upgrade "Upgrading"
    ;;
  rollback)
    rollback
    ;;
  -h | --help)
    usage
    ;;
  *)
    usage >&2
    die "unknown command: ${COMMAND}"
    ;;
esac
