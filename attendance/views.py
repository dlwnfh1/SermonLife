from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from core.models import Church, UserProfile
from core.views import _build_church_nav_context, _get_access_scope_church, _get_user_church

from .forms import (
    AttendanceDistrictForm,
    AttendanceDistrictLeaderForm,
    AttendanceGroupForm,
    AttendanceMemberForm,
)
from .models import (
    AttendanceControl,
    AttendanceDistrict,
    AttendanceDistrictLeader,
    AttendanceGroup,
    AttendanceMember,
    AttendanceRecord,
    AttendanceSession,
    AttendanceStatus,
)


def _is_pastor_or_admin(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    profile = UserProfile.objects.filter(user=user).first()
    return bool(profile and profile.member_role == "pastor")


def _can_force_open_attendance(user):
    return bool(user.is_authenticated and (user.is_superuser or user.is_staff))


def _has_attendance_check_override(user):
    if not user.is_authenticated:
        return False
    profile = UserProfile.objects.filter(user=user).only("can_check_attendance").first()
    return bool(profile and profile.can_check_attendance)


def _get_scope_church(user):
    scope_church = _get_access_scope_church(user)
    if scope_church is None:
        scope_church = _get_user_church(user)
    return scope_church or Church.get_default()


def _build_attendance_role_context(user, church):
    district_ids = list(
        AttendanceDistrictLeader.objects.filter(
            linked_user=user,
            district__church=church,
            district__is_active=True,
        ).values_list("district_id", flat=True)
    )
    led_group_ids = list(
        AttendanceGroup.objects.filter(
            leader__linked_user=user,
            church=church,
            is_active=True,
        ).values_list("id", flat=True)
    )
    return {
        "is_pastor_or_admin": _is_pastor_or_admin(user),
        "district_ids": district_ids,
        "led_group_ids": led_group_ids,
    }


def _can_access_attendance(user):
    if not user.is_authenticated:
        return False
    if _is_pastor_or_admin(user):
        return True
    if AttendanceDistrictLeader.objects.filter(linked_user=user).exists():
        return True
    if AttendanceGroup.objects.filter(leader__linked_user=user, is_active=True).exists():
        return True
    return False


def _can_submit_attendance(user, role_context):
    return bool(role_context["led_group_ids"] or _has_attendance_check_override(user))


def _is_attendance_test_sunday(request):
    return bool(settings.DEBUG and request.GET.get("force_attendance_sunday") == "1")


def _last_sunday_for(date_value):
    days_since_sunday = (date_value.weekday() + 1) % 7
    return date_value - timedelta(days=days_since_sunday)


def _get_attendance_control(church):
    control, _ = AttendanceControl.get_or_create_for_church(church)
    return control


def _is_attendance_check_day(request, church):
    return (
        timezone.localdate().weekday() == 6
        or _get_attendance_control(church).force_open
        or _is_attendance_test_sunday(request)
    )


def _scoped_group_queryset(church, role_context):
    queryset = AttendanceGroup.objects.filter(church=church, is_active=True)
    if not role_context["is_pastor_or_admin"]:
        if role_context["district_ids"]:
            queryset = queryset.filter(district_id__in=role_context["district_ids"])
        elif role_context["led_group_ids"]:
            queryset = queryset.filter(pk__in=role_context["led_group_ids"])
    return queryset


def _ensure_manage_attendance(request):
    if not _is_pastor_or_admin(request.user):
        messages.info(request, "조직 관리는 목회자와 어드민만 사용할 수 있습니다.")
        return redirect("attendance:dashboard")
    return None


@login_required(login_url="core:login")
def attendance_dashboard_view(request):
    if not _can_access_attendance(request.user):
        return render(
            request,
            "attendance/dashboard.html",
            {
                "attendance_access_denied": True,
                "active_attendance_tab": "dashboard",
                **_build_church_nav_context(_get_user_church(request.user)),
            },
        )

    church = _get_scope_church(request.user)
    role_context = _build_attendance_role_context(request.user, church)
    control = _get_attendance_control(church)
    attendance_check_day = _is_attendance_check_day(request, church)
    can_submit_attendance = _can_submit_attendance(request.user, role_context)

    if (
        attendance_check_day
        and role_context["led_group_ids"]
        and not role_context["is_pastor_or_admin"]
        and not role_context["district_ids"]
    ):
        suffix = "?force_attendance_sunday=1" if _is_attendance_test_sunday(request) else ""
        return redirect(f"{reverse('attendance:check')}{suffix}")

    district_queryset = AttendanceDistrict.objects.filter(church=church, is_active=True)
    group_queryset = _scoped_group_queryset(church, role_context)
    member_queryset = AttendanceMember.objects.filter(church=church, is_active=True)
    session_queryset = AttendanceSession.objects.filter(church=church)

    if not role_context["is_pastor_or_admin"]:
        if role_context["district_ids"]:
            district_queryset = district_queryset.filter(pk__in=role_context["district_ids"])
            member_queryset = member_queryset.filter(group__district_id__in=role_context["district_ids"])
        elif role_context["led_group_ids"]:
            member_queryset = member_queryset.filter(group_id__in=role_context["led_group_ids"])
            district_queryset = district_queryset.filter(groups__in=group_queryset).distinct()

    if attendance_check_day:
        current_session, _ = AttendanceSession.get_or_create_current(church, request.user)
    else:
        current_session = session_queryset.order_by("-worship_date", "-id").first()

    visible_group_ids = list(group_queryset.values_list("id", flat=True))
    visible_records = AttendanceRecord.objects.none()
    if current_session is not None:
        visible_records = AttendanceRecord.objects.filter(
            session=current_session,
            member__group_id__in=visible_group_ids,
        )

    status_totals = {
        row["status"]: row["total"]
        for row in visible_records.values("status").annotate(total=Count("id"))
    }

    district_cards = list(
        district_queryset.prefetch_related("leaders")
        .annotate(
            active_group_count=Count("groups", filter=Q(groups__is_active=True), distinct=True),
            active_member_count=Count("groups__members", filter=Q(groups__members__is_active=True), distinct=True),
        )
        .order_by("sort_order", "name")
    )
    group_cards = list(
        group_queryset.select_related("district", "leader")
        .annotate(active_member_count=Count("members", filter=Q(members__is_active=True), distinct=True))
        .order_by("district__sort_order", "sort_order", "name")
    )
    recent_sessions = list(session_queryset.order_by("-worship_date", "-id")[:6])

    return render(
        request,
        "attendance/dashboard.html",
        {
            "active_church": church,
            "active_attendance_tab": "dashboard",
            "current_session": current_session,
            "district_cards": district_cards,
            "group_cards": group_cards,
            "recent_sessions": recent_sessions,
            "district_count": district_queryset.count(),
            "group_count": group_queryset.count(),
            "member_count": member_queryset.count(),
            "submitted_count": visible_records.count(),
            "present_count": status_totals.get(AttendanceStatus.PRESENT, 0),
            "online_count": status_totals.get(AttendanceStatus.ONLINE, 0),
            "absent_count": status_totals.get(AttendanceStatus.ABSENT, 0),
            "excused_count": status_totals.get(AttendanceStatus.EXCUSED, 0),
            "can_manage_attendance": role_context["is_pastor_or_admin"],
            "is_attendance_group_leader": bool(role_context["led_group_ids"]),
            "is_attendance_district_leader": bool(role_context["district_ids"]),
            "can_check_attendance": bool(group_queryset.exists()) and can_submit_attendance,
            "attendance_check_day": attendance_check_day,
            "attendance_test_mode": _is_attendance_test_sunday(request),
            "attendance_force_open": control.force_open,
            "can_force_attendance_open": _can_force_open_attendance(request.user),
            **_build_church_nav_context(church),
        },
    )


@login_required(login_url="core:login")
def attendance_check_view(request):
    if not _can_access_attendance(request.user):
        return redirect("attendance:dashboard")

    church = _get_scope_church(request.user)
    role_context = _build_attendance_role_context(request.user, church)
    if not _can_submit_attendance(request.user, role_context):
        messages.info(request, "출석 체크 입력은 기본적으로 속장만 사용할 수 있습니다. 필요하면 어드민이 사용자별 출석 체크 권한을 열 수 있습니다.")
        return redirect("attendance:dashboard")

    control = _get_attendance_control(church)
    attendance_check_day = _is_attendance_check_day(request, church)
    group_queryset = (
        _scoped_group_queryset(church, role_context)
        .select_related("district", "leader")
        .annotate(active_member_count=Count("members", filter=Q(members__is_active=True), distinct=True))
        .order_by("district__sort_order", "sort_order", "name")
    )
    group_cards = list(group_queryset)

    selected_group = None
    selected_group_id = request.POST.get("group") or request.GET.get("group")
    if selected_group_id:
        try:
            selected_group = next(group for group in group_cards if group.pk == int(selected_group_id))
        except (StopIteration, ValueError):
            selected_group = None
    if selected_group is None and group_cards:
        selected_group = group_cards[0]

    if attendance_check_day:
        today = timezone.localdate()
        reference_date = today if today.weekday() == 6 else _last_sunday_for(today)
        current_session, _ = AttendanceSession.get_or_create_current(
            church,
            request.user,
            reference_date=reference_date,
        )
    else:
        current_session = AttendanceSession.objects.filter(church=church).order_by("-worship_date", "-id").first()

    members = []
    record_map = {}
    if selected_group is not None:
        members = list(
            AttendanceMember.objects.filter(group=selected_group, is_active=True)
            .select_related("group", "group__district")
            .order_by("sort_order", "name", "id")
        )
        existing_records = AttendanceRecord.objects.filter(session=current_session, member__in=members)
        record_map = {record.member_id: record for record in existing_records}

    if request.method == "POST":
        if not attendance_check_day:
            messages.error(request, "출석 체크는 주일에만 열립니다. 로컬 테스트는 force_attendance_sunday=1 로 확인해 주세요.")
            return redirect("attendance:check")
        if selected_group is None:
            messages.error(request, "출석을 입력할 속을 먼저 선택해 주세요.")
        else:
            now = timezone.now()
            records_to_create = []
            updated_count = 0
            for member in members:
                status = request.POST.get(f"status_{member.pk}") or AttendanceStatus.ABSENT
                note = (request.POST.get(f"note_{member.pk}") or "").strip()
                record = record_map.get(member.pk)
                if record is None:
                    records_to_create.append(
                        AttendanceRecord(
                            session=current_session,
                            member=member,
                            status=status,
                            note=note,
                            marked_by=request.user,
                            marked_at=now,
                        )
                    )
                else:
                    changed = False
                    if record.status != status:
                        record.status = status
                        changed = True
                    if record.note != note:
                        record.note = note
                        changed = True
                    if record.marked_by_id != request.user.id:
                        record.marked_by = request.user
                        changed = True
                    if changed or record.marked_at is None:
                        record.marked_at = now
                        record.save(update_fields=["status", "note", "marked_by", "marked_at", "updated_at"])
                        updated_count += 1
            if records_to_create:
                AttendanceRecord.objects.bulk_create(records_to_create)
            saved_total = updated_count + len(records_to_create)
            if saved_total:
                messages.success(
                    request,
                    f"{selected_group.district.name} {selected_group.name} 출석을 저장했습니다. ({saved_total}명 반영)",
                )
            else:
                messages.info(request, "변경된 내용이 없어 기존 출석표를 그대로 유지했습니다.")
            return redirect(f"{reverse('attendance:check')}?group={selected_group.pk}")

    member_rows = []
    for member in members:
        record = record_map.get(member.pk)
        member_rows.append(
            {
                "member": member,
                "status": record.status if record else AttendanceStatus.ABSENT,
                "note": record.note if record else "",
            }
        )

    return render(
        request,
        "attendance/check.html",
        {
            "active_church": church,
            "active_attendance_tab": "check",
            "group_cards": group_cards,
            "selected_group": selected_group,
            "current_session": current_session,
            "member_rows": member_rows,
            "status_choices": AttendanceStatus.choices,
            "can_manage_attendance": role_context["is_pastor_or_admin"],
            "is_attendance_group_leader": bool(role_context["led_group_ids"]),
            "is_attendance_district_leader": bool(role_context["district_ids"]),
            "attendance_check_day": attendance_check_day,
            "attendance_test_mode": _is_attendance_test_sunday(request),
            "attendance_force_open": control.force_open,
            "can_force_attendance_open": _can_force_open_attendance(request.user),
            **_build_church_nav_context(church),
        },
    )


@login_required(login_url="core:login")
def attendance_force_open_toggle_view(request):
    if request.method != "POST" or not _can_force_open_attendance(request.user):
        return redirect("attendance:dashboard")

    church = _get_scope_church(request.user)
    control = _get_attendance_control(church)
    control.force_open = not control.force_open
    control.updated_by = request.user
    control.save(update_fields=["force_open", "updated_by", "updated_at"])

    if control.force_open:
        messages.success(request, "출석 강제 공개를 켰습니다. 평일에도 가장 최근 주일 출석 체크 화면을 열 수 있습니다.")
    else:
        messages.success(request, "출석 강제 공개를 껐습니다. 이제 주일에만 출석 체크가 열립니다.")
    return redirect("attendance:dashboard")


@login_required(login_url="core:login")
def attendance_reports_view(request):
    if not _can_access_attendance(request.user):
        return redirect("attendance:dashboard")

    church = _get_scope_church(request.user)
    role_context = _build_attendance_role_context(request.user, church)
    session_queryset = AttendanceSession.objects.filter(church=church).order_by("-worship_date", "-id")
    selected_session = session_queryset.first()
    session_id = request.GET.get("session")
    if session_id:
        try:
            selected_session = session_queryset.get(pk=int(session_id))
        except (AttendanceSession.DoesNotExist, ValueError):
            pass

    group_queryset = _scoped_group_queryset(church, role_context)

    report_rows = []
    if selected_session:
        rows_queryset = (
            AttendanceRecord.objects.filter(session=selected_session, member__group__in=group_queryset)
            .values("member__group__district__name", "member__group__name", "status")
            .annotate(total=Count("id"))
            .order_by("member__group__district__name", "member__group__name")
        )
        buckets = {}
        for row in rows_queryset:
            key = (row["member__group__district__name"], row["member__group__name"])
            bucket = buckets.setdefault(
                key,
                {
                    "district_name": row["member__group__district__name"],
                    "group_name": row["member__group__name"],
                    "present": 0,
                    "online": 0,
                    "absent": 0,
                    "excused": 0,
                    "total": 0,
                },
            )
            bucket[row["status"]] = row["total"]
            bucket["total"] += row["total"]
        report_rows = list(buckets.values())

    paginator = Paginator(report_rows, 12)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(
        request,
        "attendance/reports.html",
        {
            "active_church": church,
            "active_attendance_tab": "reports",
            "selected_session": selected_session,
            "available_sessions": list(session_queryset[:12]),
            "page_obj": page_obj,
            "can_manage_attendance": role_context["is_pastor_or_admin"],
            **_build_church_nav_context(church),
        },
    )


@login_required(login_url="core:login")
def attendance_manage_view(request):
    guard = _ensure_manage_attendance(request)
    if guard:
        return guard

    church = _get_scope_church(request.user)
    district_count = AttendanceDistrict.objects.filter(church=church).count()
    group_count = AttendanceGroup.objects.filter(church=church).count()
    member_count = AttendanceMember.objects.filter(church=church).count()
    district_leader_count = AttendanceDistrictLeader.objects.filter(district__church=church).count()

    district_page = Paginator(
        AttendanceDistrict.objects.filter(church=church)
        .annotate(
            group_total=Count("groups", distinct=True),
            member_total=Count("groups__members", distinct=True),
        )
        .order_by("sort_order", "name"),
        10,
    ).get_page(request.GET.get("district_page"))

    group_page = Paginator(
        AttendanceGroup.objects.filter(church=church)
        .select_related("district", "leader")
        .annotate(member_total=Count("members", distinct=True))
        .order_by("district__sort_order", "sort_order", "name"),
        12,
    ).get_page(request.GET.get("group_page"))

    return render(
        request,
        "attendance/manage.html",
        {
            "active_church": church,
            "active_attendance_tab": "manage",
            "district_count": district_count,
            "group_count": group_count,
            "member_count": member_count,
            "district_leader_count": district_leader_count,
            "district_page": district_page,
            "group_page": group_page,
            "admin_district_url": reverse("admin:attendance_attendancedistrict_changelist"),
            "admin_group_url": reverse("admin:attendance_attendancegroup_changelist"),
            "admin_member_url": reverse("admin:attendance_attendancemember_changelist"),
            "admin_session_url": reverse("admin:attendance_attendancesession_changelist"),
            **_build_church_nav_context(church),
        },
    )


@login_required(login_url="core:login")
def attendance_district_manage_view(request, district_id):
    guard = _ensure_manage_attendance(request)
    if guard:
        return guard

    church = _get_scope_church(request.user)
    district = AttendanceDistrict.objects.filter(church=church, pk=district_id).prefetch_related("leaders").first()
    if district is None:
        messages.error(request, "선택한 교구를 찾을 수 없습니다.")
        return redirect("attendance:manage")

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update_district":
            form = AttendanceDistrictForm(request.POST, instance=district)
            if form.is_valid():
                form.save()
                messages.success(request, "교구 정보를 저장했습니다.")
                return redirect("attendance:manage_district", district_id=district.id)
        elif action == "add_leader":
            leader_form = AttendanceDistrictLeaderForm(request.POST, church=church)
            if leader_form.is_valid():
                leader = leader_form.save(commit=False)
                leader.district = district
                leader.save()
                messages.success(request, "교구장을 추가했습니다.")
                return redirect("attendance:manage_district", district_id=district.id)
        elif action == "add_group":
            group_form = AttendanceGroupForm(request.POST, district=district)
            if group_form.is_valid():
                group = group_form.save(commit=False)
                group.church = church
                group.district = district
                group.save()
                messages.success(request, "속을 추가했습니다.")
                return redirect("attendance:manage_district", district_id=district.id)
        elif action and action.startswith("save_leader:"):
            leader_id = int(action.split(":", 1)[1])
            leader = AttendanceDistrictLeader.objects.filter(pk=leader_id, district=district).first()
            if leader:
                leader_form = AttendanceDistrictLeaderForm(request.POST, instance=leader, church=church)
                if leader_form.is_valid():
                    leader_form.save()
                    messages.success(request, "교구장 정보를 저장했습니다.")
                    return redirect("attendance:manage_district", district_id=district.id)
        elif action and action.startswith("delete_leader:"):
            leader_id = int(action.split(":", 1)[1])
            deleted, _ = AttendanceDistrictLeader.objects.filter(pk=leader_id, district=district).delete()
            if deleted:
                messages.success(request, "교구장을 삭제했습니다.")
            return redirect("attendance:manage_district", district_id=district.id)
    district_form = AttendanceDistrictForm(instance=district)
    add_leader_form = AttendanceDistrictLeaderForm(church=church)
    add_group_form = AttendanceGroupForm(district=district)
    groups = list(
        AttendanceGroup.objects.filter(district=district)
        .select_related("leader")
        .annotate(member_total=Count("members", distinct=True))
        .order_by("sort_order", "name")
    )
    leader_forms = [
        (leader, AttendanceDistrictLeaderForm(instance=leader, church=church))
        for leader in district.leaders.all()
    ]

    return render(
        request,
        "attendance/manage_district.html",
        {
            "active_church": church,
            "active_attendance_tab": "manage",
            "district": district,
            "district_form": district_form,
            "add_leader_form": add_leader_form,
            "leader_forms": leader_forms,
            "groups": groups,
            "add_group_form": add_group_form,
            **_build_church_nav_context(church),
        },
    )


@login_required(login_url="core:login")
def attendance_group_manage_view(request, group_id):
    guard = _ensure_manage_attendance(request)
    if guard:
        return guard

    church = _get_scope_church(request.user)
    group = (
        AttendanceGroup.objects.filter(church=church, pk=group_id)
        .select_related("district", "leader")
        .first()
    )
    if group is None:
        messages.error(request, "선택한 속을 찾을 수 없습니다.")
        return redirect("attendance:manage")

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update_group":
            group_form = AttendanceGroupForm(request.POST, instance=group, group=group, district=group.district)
            if group_form.is_valid():
                group_form.save()
                messages.success(request, "속 정보를 저장했습니다.")
                return redirect("attendance:manage_group", group_id=group.id)
        elif action == "add_member":
            member_form = AttendanceMemberForm(request.POST, church=church)
            if member_form.is_valid():
                member = member_form.save(commit=False)
                member.church = church
                member.group = group
                member.save()
                messages.success(request, "속원을 추가했습니다.")
                return redirect("attendance:manage_group", group_id=group.id)
        elif action and action.startswith("save_member:"):
            member_id = int(action.split(":", 1)[1])
            member = AttendanceMember.objects.filter(pk=member_id, group=group).first()
            if member:
                member_form = AttendanceMemberForm(request.POST, instance=member, church=church)
                if member_form.is_valid():
                    member_form.save()
                    messages.success(request, "속원 정보를 저장했습니다.")
                    return redirect("attendance:manage_group", group_id=group.id)
        elif action and action.startswith("delete_member:"):
            member_id = int(action.split(":", 1)[1])
            deleting_leader = group.leader_id == member_id
            deleted, _ = AttendanceMember.objects.filter(pk=member_id, group=group).delete()
            if deleted:
                if deleting_leader:
                    group.leader = None
                    group.save(update_fields=["leader", "updated_at"])
                messages.success(request, "속원을 삭제했습니다.")
            return redirect("attendance:manage_group", group_id=group.id)

    group_form = AttendanceGroupForm(instance=group, group=group, district=group.district)
    add_member_form = AttendanceMemberForm(church=church)
    members = list(AttendanceMember.objects.filter(group=group).order_by("sort_order", "name", "id"))
    member_forms = [(member, AttendanceMemberForm(instance=member, church=church)) for member in members]

    return render(
        request,
        "attendance/manage_group.html",
        {
            "active_church": church,
            "active_attendance_tab": "manage",
            "group": group,
            "group_form": group_form,
            "members": members,
            "member_forms": member_forms,
            "add_member_form": add_member_form,
            **_build_church_nav_context(church),
        },
    )
