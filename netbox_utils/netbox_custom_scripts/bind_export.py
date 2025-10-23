from pathlib import Path
import subprocess
from netbox_dns.models import View, Zone, Record
from extras.scripts import Script
from jinja2 import Environment, DictLoader


ZONE_TEMPLATE = """\
;
; Zone file for zone {{ zone.name }} [{{ zone.view.name }}]
;

$TTL {{ zone.default_ttl }}

{% for record in records -%}
{{ record.name.ljust(32) }}    {{ (record.ttl|string if record.ttl is not none else '').ljust(8) }} IN {{ record.type.ljust(8) }}    {{ record.value }}
{% endfor %}\
"""


def rm_tree(path):
    for child in path.iterdir():
        if child.is_file():
            child.unlink()
        else:
            rm_tree(child)
    path.rmdir()


class ZoneExporter(Script):
    class Meta:
        name = "Zone Exporter"
        description = (
            "Export NetBox DNS zones to BIND zone files with hardcoded options"
        )
        commit_default = True

    def run(self, data, commit):
        # Hardcoded config
        export_base = Path("/services/bind")
        export_path = export_base / "zonefiles"
        remove_existing_data = True

        jinja_env = Environment(
            loader=DictLoader({"zone_file": ZONE_TEMPLATE}), autoescape=True
        )
        template = jinja_env.get_template("zone_file")

        # Step 1: chown to netbox:netbox
        try:
            self.log_info("Temporarily changing ownership to netbox:netbox")
            subprocess.run(
                ["/usr/bin/sudo", "chown", "-R", "netbox:netbox", str(export_base)],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            self.log_failure(
                f"Failed to change ownership to netbox:netbox:\nSTDERR: {e.stderr}"
            )
            return

        try:
            if remove_existing_data and export_path.exists():
                self.log_info(f"Deleting the old export path {export_path}")
                try:
                    rm_tree(export_path)
                except OSError as exc:
                    self.log_failure(f"Could not remove the old export tree: {exc}")
                    return

            try:
                export_path.mkdir(parents=False, exist_ok=True)
            except OSError as exc:
                self.log_failure(f"Could not create the export path: {exc}")
                return

            views = View.objects.all()
            for view in views:
                zones = Zone.objects.filter(view=view, active=True)
                if zones:
                    self.log_info(f"Exporting zones for view '{view.name}'")
                    self.export_zones(zones, view.name, export_path, template)

        finally:
            # restore to bind:bind before restarting named
            self.log_info("Restoring ownership to bind:bind")
            try:
                subprocess.run(
                    ["/usr/bin/sudo", "chown", "-R", "bind:bind", str(export_base)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as e:
                self.log_failure(
                    f"Failed to restore ownership to bind:bind:\nSTDERR: {e.stderr}"
                )
                return

        # restart named
        self.log_info("Attempting to refresh 'named' using rndc")
        try:
            subprocess.run(
                ["/usr/bin/sudo", "/services/bind/scripts/bind_reconfig.sh"],
                check=True,
                capture_output=True,
                text=True,
            )
            self.log_success("Successfully restarted 'named'.")
        except subprocess.CalledProcessError as e:
            self.log_failure(f"Failed to restart 'named': {e.stderr.strip() or e}")

    def export_zones(self, zones, view_name, export_path, template):
        view_path = export_path / view_name

        try:
            view_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.log_failure(f"Could not create directory {view_path}: {exc}")
            return

        for zone in zones:
            self.log_info(f"Exporting zone {zone}")
            records = Record.objects.filter(zone=zone, active=True)

            zone_data = template.render({"zone": zone, "records": records})
            zone_file_path = view_path / f"{zone.name}"

            try:
                with open(zone_file_path, "wb") as zone_file:
                    zone_file.write(zone_data.encode("UTF-8"))
            except OSError as exc:
                self.log_failure(f"Could not create zone file {zone_file_path}: {exc}")
