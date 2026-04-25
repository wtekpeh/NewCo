from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from recipe_engine.services.scale_store import recalibrate_and_store


class Command(BaseCommand):
    help = "Recalculate ingredient scale factors from historical cooking logs and store them."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--tau_days",
            type=float,
            default=14.0,
            help="Exponential decay window in days (default: 14.0).",
        )
        parser.add_argument(
            "--branch_id",
            type=int,
            default=None,
            help="Optional branch scope for recalibration.",
        )
        parser.add_argument(
            "--recipe_id",
            type=int,
            default=None,
            help="Optional recipe scope for recalibration.",
        )
        parser.add_argument(
            "--window_batches",
            type=int,
            default=30,
            help="Number of recent batches to use for event-based recalibration (default: 30).",
        )

        parser.add_argument(
            "--min_batches",
            type=int,
            default=20,
            help="Minimum unique batches required before recalibration is allowed (default: 5).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        tau_days = options["tau_days"]
        window_batches = options["window_batches"]
        min_batches = options["min_batches"]
        branch_id = options.get("branch_id")
        recipe_id = options.get("recipe_id")

        if tau_days <= 0:
            raise CommandError("tau_days must be > 0.")

        if window_batches <= 0:
            raise CommandError("window_batches must be > 0.")

        if min_batches <= 0:
            raise CommandError("min_batches must be > 0.")

        if min_batches > window_batches:
            raise CommandError("min_batches cannot be greater than window_batches.")

        self.stdout.write(
            self.style.NOTICE(
                f"Running recalibration with tau_days={tau_days}, "
                f"window_batches={window_batches}, min_batches={min_batches}, "
                f"branch_id={branch_id}, recipe_id={recipe_id}"
            )
        )

        try:
            saved_df = recalibrate_and_store(
                tau_days=tau_days,
                branch_id=branch_id,
                recipe_id=recipe_id,
                window_batches=window_batches,
                min_batches=min_batches,
            )
        except Exception as exc:
            raise CommandError(f"Recalibration failed: {exc}") from exc

        if saved_df.empty:
            self.stdout.write(
                self.style.WARNING(
                    "No scales were generated. Check that usable actual_g logs exist."
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(f"Saved {len(saved_df)} ingredient scale(s).")
        )

        for _, row in saved_df.iterrows():
            self.stdout.write(
                f"- {row['ingredient']}: "
                f"s={float(row['s']):.6f}, "
                f"tau_days={float(row['tau_days']):.1f}, "
                f"sample_count={int(row['sample_count'])}, "
                f"computed_at={row['computed_at']}"
            )
