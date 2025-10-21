from extras.scripts import Script, ObjectVar, StringVar, IntegerVar
from ipam.models import Prefix
from netaddr import IPNetwork, IPAddress
from typing import List, Tuple, Optional
import re


class FindAvailableSubprefixes(Script):
    """
    List available subprefixes (e.g., /24s) inside a selected container prefix.

    - Only container prefixes are selectable in the dropdown (status=container).
    - Desired length can be given as "24", "/24", or "10.0.0.0/24".
    - Results are VRF-scoped to the selected container's VRF (or Global if None).
    - Efficient scan that jumps over occupied regions rather than brute-forcing.
    """

    class Meta:
        name = "Find Available Subprefixes"
        description = "Return available subprefixes (e.g., /24) inside a chosen container."
        commit_default = False  # Read-only script

    # Only show container prefixes in the picker
    container = ObjectVar(
        model=Prefix,
        description="Select a container prefix (status=container).",
        query_params={"status": "container"},  # uses API filters to narrow choices
        # If your NetBox version doesn't support query_params, uncomment the next line:
        # queryset=Prefix.objects.filter(status="container"),
    )

    # Accept /24, 24, or full CIDR (we'll extract the length)
    desired_length = StringVar(
        description="Desired prefix length (e.g., 24, /24, or 10.0.0.0/24)."
    )

    limit = IntegerVar(
        required=False,
        description="Optional max results to return (recommended for huge containers)."
    )

    # ---------- Internal helpers ----------

    @staticmethod
    def _to_range(net: IPNetwork) -> Tuple[int, int]:
        return int(net.first), int(net.last)

    @staticmethod
    def _round_up(x: int, step: int) -> int:
        return ((x + step - 1) // step) * step

    @staticmethod
    def _block_size(version: int, prefixlen: int) -> int:
        bits = 32 if version == 4 else 128
        return 1 << (bits - prefixlen)

    @staticmethod
    def _status_slug(obj) -> Optional[str]:
        """
        Works across NetBox versions:
        - v3/v4 typically have obj.status as a Status object with .slug
        - older may have a plain string
        """
        st = getattr(obj, "status", None)
        if st is None:
            return None
        slug = getattr(st, "slug", None)
        return slug if slug is not None else (st if isinstance(st, str) else None)

    def _parse_desired_len(self, raw: str, container_net: IPNetwork) -> int:
        """
        Parse desired length from inputs like "24", "/24", "10.0.0.0/24", or "2001:db8::/48".
        Ensures IP family consistency if a CIDR is provided.
        """
        s = (raw or "").strip()

        # Forms: "/24" or "24"
        m = re.fullmatch(r"/?(\d{1,3})", s)
        if m:
            return int(m.group(1))

        # Form: "x.x.x.x/len" or "xxxx::/len"
        try:
            cidr = IPNetwork(s)
            if cidr.version != container_net.version:
                raise ValueError(f"Desired length family mismatch: {cidr.version} vs container {container_net.version}")
            return int(cidr.prefixlen)
        except Exception:
            pass

        raise ValueError(f"Unable to parse desired length from '{raw}'. Use 24, /24, or a CIDR like 10.0.0.0/24.")

    def _child_ranges(
        self,
        container_net: IPNetwork,
        container: Prefix
    ) -> List[Tuple[int, int]]:
        """
        Collect child prefix ranges (as integer intervals) within the container,
        restricted to the same VRF and IP family. Any existing child prefix of any status
        (active/reserved/container/deprecated) is treated as occupied space.
        """
        cvrf = container.vrf  # may be None (Global)
        version = container_net.version
        c_first, c_last = self._to_range(container_net)

        # Scoped by VRF. We'll clip to the container's bounds below.
        qs = Prefix.objects.filter(vrf=cvrf).exclude(id=container.id)
        ranges: List[Tuple[int, int]] = []
        for p in qs.only("prefix"):
            try:
                pn = IPNetwork(str(p.prefix))
            except Exception:
                continue
            if pn.version != version:
                continue
            pf, pl = self._to_range(pn)
            # Keep only ranges fully within the container
            if pf >= c_first and pl <= c_last:
                ranges.append((pf, pl))

        # Merge overlapping/adjacent ranges to speed scanning
        ranges.sort()
        merged: List[Tuple[int, int]] = []
        for rf, rl in ranges:
            if not merged or rf > merged[-1][1] + 1:
                merged.append((rf, rl))
            else:
                lf, ll = merged[-1]
                merged[-1] = (lf, max(ll, rl))

        return merged

    def _scan_available(
        self,
        container_net: IPNetwork,
        desired_len: int,
        occupied: List[Tuple[int, int]],
        limit: Optional[int] = None,
    ) -> List[str]:
        """
        Efficiently scan the container for available blocks of desired_len,
        skipping past occupied ranges. Returns CIDR strings.
        """
        version = container_net.version
        c_first, c_last = self._to_range(container_net)
        block = self._block_size(version, desired_len)

        # Align start to a desired_len boundary
        start = self._round_up(c_first, block)

        results: List[str] = []
        occ_i = 0
        n_occ = len(occupied)

        while start + block - 1 <= c_last:
            end = start + block - 1

            # Advance occupied pointer past ranges that end before this block
            while occ_i < n_occ and occupied[occ_i][1] < start:
                occ_i += 1

            # If current occupied range overlaps this block, jump after it
            if occ_i < n_occ and occupied[occ_i][0] <= end:
                start = self._round_up(occupied[occ_i][1] + 1, block)
                continue

            # Free block!
            cidr = f"{IPAddress(start)}/{desired_len}"
            results.append(cidr)
            if limit and len(results) >= limit:
                break

            # Next candidate
            start += block

        return results

    # ---------- Script entrypoint ----------

    def run(self, data, commit):
        container: Prefix = data["container"]
        raw_len: str = data["desired_length"]
        limit: Optional[int] = data.get("limit")

        # Guardrail: ensure the picked prefix is actually a container
        status_slug = self._status_slug(container)
        if status_slug != "container":
            self.log_failure(f"Selected prefix {container.prefix} is not a container (status={status_slug}).")
            return

        # Normalize container network
        try:
            cnet = IPNetwork(str(container.prefix))
        except Exception as e:
            self.log_failure(f"Invalid container prefix: {container} ({e})")
            return

        # Parse desired length from flexible input
        try:
            desired_len = self._parse_desired_len(raw_len, cnet)
        except ValueError as e:
            self.log_failure(str(e))
            return

        version = cnet.version
        max_bits = 32 if version == 4 else 128

        # Validate desired length
        if desired_len < cnet.prefixlen or desired_len > max_bits:
            self.log_failure(
                f"Desired length /{desired_len} must satisfy: "
                f"container length /{cnet.prefixlen} ≤ desired ≤ {max_bits}."
            )
            return

        # Safety: estimate candidate count (upper bound)
        est_blocks = 1 << (desired_len - cnet.prefixlen) if desired_len >= cnet.prefixlen else 0
        if est_blocks > 100000 and not limit:
            self.log_info(
                f"Estimated candidates: {est_blocks:,}. Consider setting a 'limit' to avoid huge output."
            )

        occ = self._child_ranges(cnet, container)
        self.log_info(
            f"Container: {cnet} (VRF: {container.vrf or 'Global'}), desired=/{desired_len}, "
            f"merged child ranges: {len(occ)}"
        )

        available = self._scan_available(cnet, desired_len, occ, limit=limit)

        if not available:
            self.log_warning("No available subprefixes found at the requested size.")
            return

        self.log_success(f"Found {len(available)} available /{desired_len} inside {cnet}:")
        for cidr in available[:50]:
            self.log_info(f" - {cidr}")
        if len(available) > 50:
            self.log_info(f"... and {len(available) - 50} more.")

        # Compact copy/paste list
        self.log_info("Copy/paste list below:")
        self.log_info("\n" + "\n".join(available))
