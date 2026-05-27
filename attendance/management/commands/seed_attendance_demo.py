from random import Random

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Church

from attendance.models import (
    AttendanceGroup,
    AttendanceMember,
    AttendanceRecord,
    AttendanceSession,
    AttendanceStatus,
)


class Command(BaseCommand):
    help = "Fill the latest Sunday attendance session with demo present/absent data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--seed",
            type=int,
            default=20260526,
            help="Random seed for reproducible demo data.",
        )
        parser.add_argument(
            "--present-rate",
            type=float,
            default=0.76,
            help="Probability that a member is marked present.",
        )

    def handle(self, *args, **options):
        rng = Random(options["seed"])
        present_rate = max(0.0, min(1.0, options["present_rate"]))
        today = timezone.localdate()
        marker = get_user_model().objects.order_by("id").first()
        churches = Church.objects.filter(attendance_groups__isnull=False).distinct()

        if not churches.exists():
            self.stdout.write(self.style.WARNING("출석 조직도가 있는 교회가 없습니다."))
            return

        for church in churches:
            session, _ = AttendanceSession.get_or_create_current(
                church,
                marker,
                reference_date=today,
            )
            groups = AttendanceGroup.objects.filter(church=church, is_active=True).order_by(
                "district__sort_order",
                "sort_order",
                "id",
            )
            members = list(
                AttendanceMember.objects.filter(church=church, is_active=True).select_related("group")
            )
            AttendanceRecord.objects.filter(session=session, member__in=members).delete()

            now = timezone.now()
            records = []
            for member in members:
                status = (
                    AttendanceStatus.PRESENT
                    if rng.random() < present_rate
                    else AttendanceStatus.ABSENT
                )
                records.append(
                    AttendanceRecord(
                        session=session,
                        member=member,
                        status=status,
                        marked_by=marker,
                        marked_at=now,
                        note="",
                    )
                )
            AttendanceRecord.objects.bulk_create(records)

            present = sum(1 for record in records if record.status == AttendanceStatus.PRESENT)
            absent = len(records) - present
            self.stdout.write(
                self.style.SUCCESS(
                    f"{church.name}: {groups.count()}속 / {len(records)}명 / 출석 {present} / 결석 {absent} / {session.worship_date}"
                )
            )
