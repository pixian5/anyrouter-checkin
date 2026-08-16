#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
project_root="$(cd -- "$script_dir/.." && pwd -P)"
template_dir="$script_dir/systemd"
render_only=false
unit_output_dir=/etc/systemd/system

if [ "$#" -eq 2 ] && [ "$1" = "--render-only" ]; then
	render_only=true
	unit_output_dir="$2"
elif [ "$#" -ne 0 ]; then
	printf 'Usage: %s [--render-only OUTPUT_DIR]\n' "$0" >&2
	exit 2
fi

case "$project_root" in
	*[[:space:]%]*)
		printf 'Project path cannot contain whitespace or %%: %s\n' "$project_root" >&2
		exit 2
		;;
esac

render_template() {
	local source_file="$1"
	local target_file="$2"
	local escaped_root
	escaped_root="${project_root//\\/\\\\}"
	escaped_root="${escaped_root//&/\\&}"
	escaped_root="${escaped_root//|/\\|}"
	sed "s|@PROJECT_ROOT@|$escaped_root|g" "$source_file" > "$target_file"
}

if [ "$render_only" = true ]; then
	mkdir -p "$unit_output_dir"
	render_template "$template_dir/anyrouter-checkin.service.in" "$unit_output_dir/anyrouter-checkin.service"
	cp "$template_dir/anyrouter-checkin.timer" "$unit_output_dir/anyrouter-checkin.timer"
	printf 'Rendered systemd units for %s into %s\n' "$project_root" "$unit_output_dir"
	exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
	printf 'Run this installer as root on the server.\n' >&2
	exit 1
fi
if [ ! -s "$project_root/.env" ]; then
	printf 'Missing or empty server configuration: %s/.env\n' "$project_root" >&2
	exit 1
fi
if [ ! -x "$project_root/.venv/bin/python" ]; then
	printf 'Missing project virtual environment: %s/.venv/bin/python\n' "$project_root" >&2
	exit 1
fi

render_dir="$(mktemp -d)"
trap 'rm -rf "$render_dir"' EXIT
render_template "$template_dir/anyrouter-checkin.service.in" "$render_dir/anyrouter-checkin.service"
cp "$template_dir/anyrouter-checkin.timer" "$render_dir/anyrouter-checkin.timer"

install -m 0644 "$render_dir/anyrouter-checkin.service" "$unit_output_dir/anyrouter-checkin.service"
install -m 0644 "$render_dir/anyrouter-checkin.timer" "$unit_output_dir/anyrouter-checkin.timer"

dropin_dir="$unit_output_dir/anyrouter-checkin.service.d"
for legacy_name in env.conf overlay.conf; do
	legacy_path="$dropin_dir/$legacy_name"
	if [ -f "$legacy_path" ]; then
		mv "$legacy_path" "$legacy_path.before-portable-installer"
		printf 'Disabled legacy path override: %s\n' "$legacy_path"
	fi
done

systemctl daemon-reload
systemctl enable --now anyrouter-checkin.timer
systemctl is-active --quiet anyrouter-checkin.timer
printf 'Installed local check-in service for clone: %s\n' "$project_root"
