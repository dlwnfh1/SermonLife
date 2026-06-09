from datetime import timedelta
from math import ceil
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.core.mail import EmailMessage
from django.db.models import Count, Max, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from reportlab.graphics import renderSVG
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

from core.models import Church, UserProfile
from core.views import _build_church_nav_context, _get_access_scope_church, _get_user_church

from .forms import (
    AttendanceDistrictForm,
    AttendanceDistrictLeaderForm,
    AttendanceGroupCreateForm,
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


CHECK_STATUS_CHOICES = (
    (AttendanceStatus.PRESENT, "출석"),
    (AttendanceStatus.ABSENT, "결석"),
)
_PDF_FONT_NAME = "HYGothic-Medium"
_PDF_FONT_REGISTERED = False
_ATTENDANCE_PIN_GROUP_SESSION_KEY = "attendance_pin_group_id"
_ATTENDANCE_PIN_VERIFIED_AT_SESSION_KEY = "attendance_pin_verified_at"
_ATTENDANCE_PIN_TTL = timedelta(hours=12)
_ATTENDANCE_LAST_DISTRICT_SESSION_KEY = "attendance_last_district_id"
_ATTENDANCE_LAST_GROUP_SESSION_KEY = "attendance_last_group_id"


def attendance_pwa_manifest_view(request):
    return JsonResponse(
        {
            "id": "/attendance/check-pwa",
            "name": "FGMC 주일출석표",
            "short_name": "주일출석표",
            "description": "주일 출석을 제출하는 전용 화면입니다.",
            "start_url": "/attendance/check/",
            "scope": "/attendance/",
            "display": "standalone",
            "background_color": "#fbf3e7",
            "theme_color": "#a56a3b",
            "icons": [
                {
                    "src": f"{settings.STATIC_URL}core/icons/attendance-check-192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
                {
                    "src": f"{settings.STATIC_URL}core/icons/attendance-check-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
            ],
        }
    )


def attendance_service_worker_view(request):
    script = """
self.addEventListener('install', function(event) {
  self.skipWaiting();
});

self.addEventListener('activate', function(event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', function() {
  // no-op service worker for installability within /attendance/ scope
});
""".strip()
    response = HttpResponse(script, content_type="application/javascript; charset=utf-8")
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


def _build_attendance_check_entry_url(request):
    return request.build_absolute_uri(reverse("attendance:check"))


def _build_attendance_qr_svg(url, size=260):
    widget = qr.QrCodeWidget(url)
    bounds = widget.getBounds()
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    drawing = Drawing(size, size, transform=[size / width, 0, 0, size / height, 0, 0])
    drawing.add(widget)
    return renderSVG.drawToString(drawing)


def attendance_check_qr_svg_view(request):
    svg = _build_attendance_qr_svg(_build_attendance_check_entry_url(request))
    response = HttpResponse(svg, content_type="image/svg+xml; charset=utf-8")
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@login_required(login_url="core:login")
def attendance_check_qr_print_view(request):
    if not _can_access_attendance(request.user):
        return redirect("attendance:dashboard")

    church = _get_scope_church(request.user)
    return render(
        request,
        "attendance/check_qr_print.html",
        {
            "active_church": church,
            "active_attendance_tab": "manage",
            "check_url": _build_attendance_check_entry_url(request),
            "check_qr_svg_url": reverse("attendance:check_qr_svg"),
            **_build_church_nav_context(church),
        },
    )


def _is_pastor_or_admin(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    profile = UserProfile.objects.filter(user=user).first()
    return bool(profile and profile.member_role == "pastor")


def _ensure_pdf_font():
    global _PDF_FONT_REGISTERED
    if not _PDF_FONT_REGISTERED:
        pdfmetrics.registerFont(UnicodeCIDFont(_PDF_FONT_NAME))
        _PDF_FONT_REGISTERED = True


def _build_weekly_pdf_sections(group_queryset, selected_session, selected_district=None, selected_group=None):
    scoped_groups = (
        group_queryset.filter(is_active=True)
        .select_related("district", "guide", "leader")
        .order_by("district__sort_order", "sort_order", "name", "id")
    )
    if selected_group is not None:
        scoped_groups = scoped_groups.filter(pk=selected_group.pk)
    elif selected_district is not None:
        scoped_groups = scoped_groups.filter(district=selected_district)

    groups = list(scoped_groups)
    if not groups:
        return []

    members = list(
        AttendanceMember.objects.filter(group__in=groups, is_active=True)
        .select_related("group", "group__district")
        .order_by("group__district__sort_order", "group__sort_order", "sort_order", "name", "id")
    )
    record_map = {
        record.member_id: _normalize_attendance_status(record.status)
        for record in AttendanceRecord.objects.filter(session=selected_session, member__in=members)
    }

    members_by_group = {}
    for member in members:
        members_by_group.setdefault(member.group_id, []).append(
            {
                "name": member.name,
                "status": record_map.get(member.id, AttendanceStatus.ABSENT),
            }
        )

    sections = []
    for group in groups:
        group_members = members_by_group.get(group.id, [])
        present_count = sum(1 for member in group_members if member["status"] == AttendanceStatus.PRESENT)
        absent_count = len(group_members) - present_count
        sections.append(
            {
                "district_name": group.district.name,
                "group_name": group.name,
                "guide_name": group.guide.name if group.guide_id else "",
                "leader_name": group.leader.name if group.leader_id else "",
                "present_count": present_count,
                "absent_count": absent_count,
                "total_count": len(group_members),
                "members": group_members,
            }
        )
    return sections


def _draw_attendance_pdf_header(pdf, width, height, title, subtitle):
    pdf.setFillColor(colors.HexColor("#23170f"))
    pdf.setFont(_PDF_FONT_NAME, 21)
    pdf.drawString(24, height - 32, title)
    pdf.setFillColor(colors.HexColor("#6f5e4f"))
    pdf.setFont(_PDF_FONT_NAME, 10)
    pdf.drawRightString(width - 24, height - 28, subtitle)


def _draw_attendance_pdf_footer(pdf, width, page_number):
    pdf.setStrokeColor(colors.HexColor("#e7d8c4"))
    pdf.line(24, 18, width - 24, 18)
    pdf.setFillColor(colors.HexColor("#8c5630"))
    pdf.setFont(_PDF_FONT_NAME, 8)
    pdf.drawRightString(width - 24, 8, str(page_number))


def _draw_status_chip(pdf, x, y, label, present):
    if present:
        fill_color = colors.HexColor("#e8f0e3")
        stroke_color = colors.HexColor("#c9dbc0")
        text_color = colors.HexColor("#58724d")
    else:
        fill_color = colors.HexColor("#f8d7d3")
        stroke_color = colors.HexColor("#e4a6a0")
        text_color = colors.HexColor("#b64b45")

    pdf.setFillColor(fill_color)
    pdf.setStrokeColor(stroke_color)
    pdf.roundRect(x, y - 7, 18, 11, 5, stroke=1, fill=1)
    pdf.setFillColor(text_color)
    pdf.setFont(_PDF_FONT_NAME, 6)
    pdf.drawCentredString(x + 9, y - 3, label)


def _draw_attendance_pdf_section(pdf, x, y_top, width, section):
    member_count = len(section["members"])
    box_height = 58 + (member_count * 15)
    y = y_top - box_height

    pdf.setStrokeColor(colors.HexColor("#dfcfbb"))
    pdf.setFillColor(colors.HexColor("#fffaf2"))
    pdf.roundRect(x, y, width, box_height, 10, stroke=1, fill=1)

    pdf.setFillColor(colors.HexColor("#23170f"))
    pdf.setFont(_PDF_FONT_NAME, 13)
    pdf.drawString(x + 10, y_top - 18, section["district_name"])
    pdf.setFillColor(colors.HexColor("#8c5630"))
    pdf.setFont(_PDF_FONT_NAME, 11)
    pdf.drawRightString(x + width - 10, y_top - 18, section["group_name"])

    subparts = []
    if section["guide_name"]:
        subparts.append(f"인도자 {section['guide_name']}")
    if section["leader_name"]:
        subparts.append(f"속장 {section['leader_name']}")
    if subparts:
        pdf.setFillColor(colors.HexColor("#6f5e4f"))
        pdf.setFont(_PDF_FONT_NAME, 8)
        pdf.drawString(x + 10, y_top - 32, " · ".join(subparts))

    pdf.setFillColor(colors.HexColor("#a56a3b"))
    pdf.setFont(_PDF_FONT_NAME, 8)
    pdf.drawRightString(
        x + width - 10,
        y_top - 32,
        f"출석 {section['present_count']} / 결석 {section['absent_count']} / 총원 {section['total_count']}",
    )

    row_y = y_top - 48
    for member in section["members"]:
        pdf.setFillColor(colors.HexColor("#23170f"))
        pdf.setFont(_PDF_FONT_NAME, 9)
        pdf.drawString(x + 10, row_y, member["name"])

        if member["status"] == AttendanceStatus.PRESENT:
            fill_color = colors.HexColor("#e8f0e3")
            text_color = colors.HexColor("#58724d")
            label = "출석"
        else:
            fill_color = colors.HexColor("#f8d7d3")
            text_color = colors.HexColor("#b64b45")
            label = "결석"

        chip_width = 28
        chip_height = 12
        chip_x = x + width - chip_width - 10
        chip_y = row_y - 9
        pdf.setFillColor(fill_color)
        pdf.setStrokeColor(fill_color)
        pdf.roundRect(chip_x, chip_y, chip_width, chip_height, 6, stroke=1, fill=1)
        pdf.setFillColor(text_color)
        pdf.setFont(_PDF_FONT_NAME, 7)
        pdf.drawCentredString(chip_x + (chip_width / 2), row_y - 3, label)
        row_y -= 15

    return box_height


def _draw_attendance_pdf_section_v2(pdf, x, y_top, width, section):
    member_count = len(section["members"])
    member_columns = 2
    member_rows = max(1, ceil(member_count / member_columns))
    box_height = 50 + (member_rows * 15)
    y = y_top - box_height

    pdf.setStrokeColor(colors.HexColor("#dfcfbb"))
    pdf.setFillColor(colors.HexColor("#fffaf2"))
    pdf.roundRect(x, y, width, box_height, 10, stroke=1, fill=1)

    pdf.setFillColor(colors.HexColor("#23170f"))
    pdf.setFont(_PDF_FONT_NAME, 12)
    pdf.drawString(x + 10, y_top - 16, section["district_name"])
    pdf.setFillColor(colors.HexColor("#8c5630"))
    pdf.setFont(_PDF_FONT_NAME, 10)
    pdf.drawRightString(x + width - 10, y_top - 16, section["group_name"])

    subparts = []
    if section["guide_name"]:
        subparts.append(f"인도자 {section['guide_name']}")
    if section["leader_name"]:
        subparts.append(f"속장 {section['leader_name']}")
    if subparts:
        pdf.setFillColor(colors.HexColor("#6f5e4f"))
        pdf.setFont(_PDF_FONT_NAME, 8)
        pdf.drawString(x + 10, y_top - 28, " · ".join(subparts))

    pdf.setFillColor(colors.HexColor("#a56a3b"))
    pdf.setFont(_PDF_FONT_NAME, 8)
    pdf.drawRightString(
        x + width - 10,
        y_top - 28,
        f"출석 {section['present_count']} / 결석 {section['absent_count']} / 총원 {section['total_count']}",
    )

    pdf.setStrokeColor(colors.HexColor("#ead9c5"))
    pdf.line(x + 10, y_top - 35, x + width - 10, y_top - 35)

    inner_width = width - 20
    column_gap = 10
    column_width = (inner_width - column_gap) / member_columns
    start_y = y_top - 48

    for index, member in enumerate(section["members"]):
        row_index = index // member_columns
        column_index = index % member_columns
        col_x = x + 10 + (column_index * (column_width + column_gap))
        row_y = start_y - (row_index * 15)

        pdf.setFillColor(colors.HexColor("#23170f"))
        pdf.setFont(_PDF_FONT_NAME, 8)
        pdf.drawString(col_x, row_y, member["name"])

        is_present = member["status"] == AttendanceStatus.PRESENT
        chip_x = col_x + column_width - 20
        _draw_status_chip(pdf, chip_x, row_y, "출" if is_present else "결", is_present)

    return box_height


def _build_weekly_pdf_document(church, selected_session, group_queryset, selected_district=None, selected_group=None):
    sections = _build_weekly_pdf_sections(
        group_queryset,
        selected_session,
        selected_district=selected_district,
        selected_group=selected_group,
    )

    if selected_group is not None:
        report_title = f"{church.name} {selected_group.name} 출석표"
    elif selected_district is not None:
        report_title = f"{church.name} {selected_district.name} 교구 출석표"
    else:
        report_title = f"{church.name} 전체 출석표"

    safe_title = report_title.replace(" ", "_")
    _ensure_pdf_font()
    page_width, page_height = landscape(A4)
    subtitle = f"{selected_session.worship_date:%Y-%m-%d} 주일"

    response = HttpResponse(content_type="application/pdf")
    pdf = canvas.Canvas(response, pagesize=landscape(A4))
    pdf.setTitle(report_title)

    if not sections:
        _draw_attendance_pdf_header(pdf, page_width, page_height, report_title, subtitle)
        pdf.setFont(_PDF_FONT_NAME, 12)
        pdf.setFillColor(colors.HexColor("#6f5e4f"))
        pdf.drawString(24, page_height - 80, "출력할 출석 데이터가 없습니다.")
        _draw_attendance_pdf_footer(pdf, page_width, 1)
        pdf.showPage()
        pdf.save()
        return response.content, safe_title

    margin = 24
    gutter = 12
    columns = 3
    column_width = (page_width - (margin * 2) - (gutter * (columns - 1))) / columns
    column_x = [margin + ((column_width + gutter) * idx) for idx in range(columns)]
    column_y = [page_height - 56 for _ in range(columns)]
    _draw_attendance_pdf_header(pdf, page_width, page_height, report_title, subtitle)
    page_number = 1

    for section in sections:
        box_height = 50 + (max(1, ceil(len(section["members"]) / 2)) * 15)

        target_column = None
        for idx in range(columns):
            if column_y[idx] - box_height >= margin:
                target_column = idx
                break

        if target_column is None:
            _draw_attendance_pdf_footer(pdf, page_width, page_number)
            pdf.showPage()
            _draw_attendance_pdf_header(pdf, page_width, page_height, report_title, subtitle)
            column_y = [page_height - 56 for _ in range(columns)]
            page_number += 1
            target_column = 0

        used_height = _draw_attendance_pdf_section_v2(
            pdf,
            column_x[target_column],
            column_y[target_column],
            column_width,
            section,
        )
        column_y[target_column] -= used_height + 10

    _draw_attendance_pdf_footer(pdf, page_width, page_number)
    pdf.showPage()
    pdf.save()
    return response.content, safe_title


def _can_force_open_attendance(user):
    return bool(user.is_authenticated and user.username == "admin")


def _has_attendance_check_override(user):
    if not user.is_authenticated:
        return False
    profile = UserProfile.objects.filter(user=user).only("can_check_attendance").first()
    return bool(profile and profile.can_check_attendance)


def _has_attendance_manage_override(user):
    if not user.is_authenticated:
        return False
    profile = UserProfile.objects.filter(user=user).only("can_manage_attendance").first()
    return bool(profile and profile.can_manage_attendance)


def _is_attendance_only_user(user):
    # Deprecated: public PIN entry replaced the old attendance-only login flow.
    return False


def _redirect_attendance_only_user_to_check(request):
    if _is_attendance_only_user(request.user):
        return redirect("attendance:check")
    return None


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
            Q(leader__linked_user=user) | Q(attendance_login_user=user),
            church=church,
            is_active=True,
        ).values_list("id", flat=True)
    )
    has_full_attendance_access = _is_pastor_or_admin(user) or _has_attendance_manage_override(user)
    return {
        "is_pastor_or_admin": _is_pastor_or_admin(user),
        "has_full_attendance_access": has_full_attendance_access,
        "district_ids": district_ids,
        "led_group_ids": led_group_ids,
    }


def _can_access_attendance(user):
    if not user.is_authenticated:
        return False
    if _is_pastor_or_admin(user) or _has_attendance_manage_override(user):
        return True
    if AttendanceDistrictLeader.objects.filter(linked_user=user).exists():
        return True
    if AttendanceGroup.objects.filter(Q(leader__linked_user=user) | Q(attendance_login_user=user), is_active=True).exists():
        return True
    return False


def _can_submit_attendance(user, role_context):
    return bool(role_context["led_group_ids"] or _has_attendance_check_override(user))


def _can_use_manual_attendance_check(user):
    if not user.is_authenticated:
        return False
    return _is_pastor_or_admin(user) or _has_attendance_manage_override(user) or _has_attendance_check_override(user)


def _is_attendance_test_sunday(request):
    return bool(settings.DEBUG and request.GET.get("force_attendance_sunday") == "1")


def _last_sunday_for(date_value):
    days_since_sunday = (date_value.weekday() + 1) % 7
    return date_value - timedelta(days=days_since_sunday)


def _get_latest_sunday_session(church, reference_date=None):
    target_date = _last_sunday_for(reference_date or timezone.localdate())
    sessions = AttendanceSession.objects.filter(church=church, worship_date__lte=target_date).order_by("-worship_date", "-id")
    for session in sessions:
        if session.worship_date.weekday() == 6:
            return session
    return None


def _get_attendance_control(church):
    control, _ = AttendanceControl.get_or_create_for_church(church)
    return control


def _is_attendance_check_day(request, church):
    return (
        timezone.localdate().weekday() == 6
        or _get_attendance_control(church).force_open
        or _is_attendance_test_sunday(request)
    )


def _get_attendance_check_church(user):
    if getattr(user, "is_authenticated", False) and _can_access_attendance(user):
        return _get_scope_church(user)
    return Church.get_default()


def _clear_attendance_pin_session(request):
    request.session.pop(_ATTENDANCE_PIN_GROUP_SESSION_KEY, None)
    request.session.pop(_ATTENDANCE_PIN_VERIFIED_AT_SESSION_KEY, None)


def _set_attendance_pin_session(request, group):
    request.session[_ATTENDANCE_PIN_GROUP_SESSION_KEY] = group.pk
    request.session[_ATTENDANCE_PIN_VERIFIED_AT_SESSION_KEY] = timezone.now().isoformat()
    request.session[_ATTENDANCE_LAST_DISTRICT_SESSION_KEY] = group.district_id
    request.session[_ATTENDANCE_LAST_GROUP_SESSION_KEY] = group.pk
    request.session.modified = True


def _get_remembered_attendance_selection(request, church):
    district_id = request.session.get(_ATTENDANCE_LAST_DISTRICT_SESSION_KEY)
    group_id = request.session.get(_ATTENDANCE_LAST_GROUP_SESSION_KEY)

    district = None
    group = None

    if district_id:
        district = AttendanceDistrict.objects.filter(
            church=church,
            is_active=True,
            pk=district_id,
        ).exclude(name="교구장").first()

    if group_id:
        group = (
            AttendanceGroup.objects.filter(
                church=church,
                is_active=True,
                district__is_active=True,
                pk=group_id,
            )
            .exclude(district__name="교구장")
            .first()
        )

    return district, group


def _get_check_screen_remembered_selection(request, church):
    district_id = request.session.get(_ATTENDANCE_LAST_DISTRICT_SESSION_KEY)
    group_id = request.session.get(_ATTENDANCE_LAST_GROUP_SESSION_KEY)

    district = None
    group = None

    if district_id:
        district = AttendanceDistrict.objects.filter(
            church=church,
            is_active=True,
            pk=district_id,
        ).first()

    if group_id:
        group = (
            AttendanceGroup.objects.filter(
                church=church,
                is_active=True,
                district__is_active=True,
                pk=group_id,
            )
            .select_related("district")
            .first()
        )

    return district, group


def _get_attendance_pin_group(request, church):
    group_id = request.session.get(_ATTENDANCE_PIN_GROUP_SESSION_KEY)
    verified_at_raw = request.session.get(_ATTENDANCE_PIN_VERIFIED_AT_SESSION_KEY)
    if not group_id or not verified_at_raw:
        return None

    try:
        verified_at = timezone.datetime.fromisoformat(verified_at_raw)
    except (TypeError, ValueError):
        _clear_attendance_pin_session(request)
        return None

    if timezone.is_naive(verified_at):
        verified_at = timezone.make_aware(verified_at, timezone.get_current_timezone())

    if timezone.now() - verified_at > _ATTENDANCE_PIN_TTL:
        _clear_attendance_pin_session(request)
        return None

    group = (
        AttendanceGroup.objects.filter(church=church, pk=group_id, is_active=True, district__is_active=True)
        .select_related("district", "guide", "leader")
        .annotate(active_member_count=Count("members", filter=Q(members__is_active=True), distinct=True))
        .first()
    )
    if group is None:
        _clear_attendance_pin_session(request)
    return group


def _scoped_group_queryset(church, role_context):
    queryset = AttendanceGroup.objects.filter(church=church, is_active=True)
    if not role_context["has_full_attendance_access"]:
        if role_context["district_ids"]:
            queryset = queryset.filter(district_id__in=role_context["district_ids"])
        elif role_context["led_group_ids"]:
            queryset = queryset.filter(pk__in=role_context["led_group_ids"])
    return queryset


def _ensure_manage_attendance(request):
    if _is_attendance_only_user(request.user):
        return redirect("attendance:check")
    if not (_is_pastor_or_admin(request.user) or _has_attendance_manage_override(request.user)):
        messages.info(request, "교구/속 관리는 목회자와 어드민만 사용할 수 있습니다.")
        return redirect("attendance:dashboard")
    return None


def _normalize_attendance_status(status_value):
    return AttendanceStatus.PRESENT if status_value == AttendanceStatus.PRESENT else AttendanceStatus.ABSENT


def _seed_demo_attendance_data(seed=20260526, present_rate=0.76):
    from random import Random

    rng = Random(seed)
    present_rate = max(0.0, min(1.0, present_rate))
    today = timezone.localdate()
    marker = get_user_model().objects.order_by("id").first()
    summaries = []

    for church in Church.objects.filter(attendance_groups__isnull=False).distinct():
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
        summaries.append(
            f"{church.name}: {groups.count()}속 / {len(records)}명 / 출석 {present} / 결석 {absent} / {session.worship_date}"
        )
    return summaries


@login_required(login_url="core:login")
def attendance_dashboard_view(request):
    guard = _redirect_attendance_only_user_to_check(request)
    if guard:
        return guard
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
        and not role_context["has_full_attendance_access"]
        and not role_context["district_ids"]
    ):
        suffix = "?force_attendance_sunday=1" if _is_attendance_test_sunday(request) else ""
        return redirect(f"{reverse('attendance:check')}{suffix}")

    district_queryset = AttendanceDistrict.objects.filter(church=church, is_active=True)
    group_queryset = _scoped_group_queryset(church, role_context)
    member_queryset = AttendanceMember.objects.filter(church=church, is_active=True)
    session_queryset = AttendanceSession.objects.filter(church=church)

    if not role_context["has_full_attendance_access"]:
        if role_context["district_ids"]:
            district_queryset = district_queryset.filter(pk__in=role_context["district_ids"])
            member_queryset = member_queryset.filter(group__district_id__in=role_context["district_ids"])
        elif role_context["led_group_ids"]:
            member_queryset = member_queryset.filter(group_id__in=role_context["led_group_ids"])
            district_queryset = district_queryset.filter(groups__in=group_queryset).distinct()

    if attendance_check_day:
        current_session, _ = AttendanceSession.get_or_create_current(
            church,
            request.user,
            reference_date=timezone.localdate(),
        )
    else:
        current_session = _get_latest_sunday_session(church)

    visible_group_ids = list(group_queryset.values_list("id", flat=True))
    visible_records = AttendanceRecord.objects.none()
    if current_session is not None:
        visible_records = AttendanceRecord.objects.filter(
            session=current_session,
            member__group_id__in=visible_group_ids,
        )

    present_count = visible_records.filter(status=AttendanceStatus.PRESENT).count()
    submitted_count = visible_records.count()
    absent_count = max(submitted_count - present_count, 0)
    submitted_group_ids = set(visible_records.values_list("member__group_id", flat=True))

    group_status_map = {}
    district_status_map = {}
    for row in visible_records.values(
        "member__group_id",
        "member__group__district_id",
        "status",
    ).annotate(total=Count("id")):
        group_bucket = group_status_map.setdefault(
            row["member__group_id"],
            {
                "present": 0,
                "absent": 0,
                "submitted": 0,
            },
        )
        district_bucket = district_status_map.setdefault(
            row["member__group__district_id"],
            {
                "present": 0,
                "absent": 0,
                "submitted": 0,
            },
        )
        total = row["total"]
        is_present = row["status"] == AttendanceStatus.PRESENT
        status_key = "present" if is_present else "absent"
        group_bucket[status_key] += total
        group_bucket["submitted"] += total
        district_bucket[status_key] += total
        district_bucket["submitted"] += total

    district_cards = list(
        district_queryset.prefetch_related("leaders")
        .annotate(
            active_group_count=Count("groups", filter=Q(groups__is_active=True), distinct=True),
            active_member_count=Count("groups__members", filter=Q(groups__members__is_active=True), distinct=True),
        )
        .order_by("sort_order", "name")
    )
    group_cards = list(
        group_queryset.select_related("district", "guide", "leader")
        .annotate(active_member_count=Count("members", filter=Q(members__is_active=True), distinct=True))
        .order_by("district__sort_order", "sort_order", "name")
    )
    for district in district_cards:
        stats = district_status_map.get(
            district.pk,
            {"present": 0, "absent": 0, "submitted": 0},
        )
        district.present_count = stats["present"]
        district.absent_count = stats["absent"]
        district.submitted_count = stats["submitted"]
        district.pending_count = max(district.active_member_count - district.submitted_count, 0)
    for group in group_cards:
        stats = group_status_map.get(
            group.pk,
            {"present": 0, "absent": 0, "submitted": 0},
        )
        group.present_count = stats["present"]
        group.absent_count = stats["absent"]
        group.submitted_count = stats["submitted"]
        group.pending_count = max(group.active_member_count - group.submitted_count, 0)
    missing_groups = [
        group
        for group in group_cards
        if group.active_member_count and group.pk not in submitted_group_ids
    ]
    recent_sessions = []
    for session in session_queryset.order_by("-worship_date", "-id"):
        if session.worship_date.weekday() == 6:
            recent_sessions.append(session)
        if len(recent_sessions) >= 6:
            break

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
            "submitted_count": submitted_count,
            "pending_count": max(member_queryset.count() - submitted_count, 0),
            "present_count": present_count,
            "absent_count": absent_count,
            "missing_groups": missing_groups,
            "missing_group_count": len(missing_groups),
            "can_manage_attendance": role_context["has_full_attendance_access"],
            "can_manual_check_attendance": _can_use_manual_attendance_check(request.user),
            "is_attendance_group_leader": bool(role_context["led_group_ids"]),
            "is_attendance_district_leader": bool(role_context["district_ids"]),
            "can_check_attendance": bool(group_queryset.exists()) and can_submit_attendance,
            "attendance_check_day": attendance_check_day,
            "attendance_test_mode": _is_attendance_test_sunday(request),
            "attendance_force_open": control.force_open,
            "can_force_attendance_open": _can_force_open_attendance(request.user),
            "can_seed_demo_attendance": bool(settings.DEBUG and role_context["has_full_attendance_access"]),
            **_build_church_nav_context(church),
        },
    )


def attendance_check_view(request):
    church = _get_attendance_check_church(request.user)
    control = _get_attendance_control(church)
    attendance_check_day = _is_attendance_check_day(request, church)
    can_force_attendance_open = _can_force_open_attendance(request.user)
    remembered_district, remembered_group = _get_check_screen_remembered_selection(request, church)

    if request.GET.get("reset") == "1":
        _clear_attendance_pin_session(request)
        return redirect("attendance:check")

    district_queryset = AttendanceDistrict.objects.filter(
        church=church,
        is_active=True,
    ).exclude(name="교구장").order_by("sort_order", "name", "id")
    district_cards = list(district_queryset)
    special_district = (
        AttendanceDistrict.objects.filter(church=church, is_active=True, name="교구장")
        .order_by("sort_order", "name", "id")
        .first()
    )
    if special_district and all(d.pk != special_district.pk for d in district_cards):
        district_cards.append(special_district)

    selected_district = None
    district_id = request.POST.get("district") or request.GET.get("district")
    if not district_id and remembered_district is not None:
        district_id = str(remembered_district.pk)
    if district_id:
        try:
            selected_district = next(d for d in district_cards if d.pk == int(district_id))
        except (StopIteration, ValueError):
            selected_district = None

    pin_group_queryset = AttendanceGroup.objects.filter(
        church=church,
        is_active=True,
        district__is_active=True,
    ).select_related("district").order_by("district__sort_order", "sort_order", "name", "id")
    if selected_district is not None:
        pin_group_queryset = pin_group_queryset.filter(district=selected_district)
    pin_groups = list(pin_group_queryset)
    selected_pin_group_id = request.POST.get("group") or request.GET.get("group")
    if not selected_pin_group_id and remembered_group is not None:
        if selected_district is None or remembered_group.district_id == selected_district.pk:
            selected_pin_group_id = str(remembered_group.pk)

    valid_pin_group_ids = {str(group.pk) for group in pin_groups}
    if selected_pin_group_id not in valid_pin_group_ids:
        selected_pin_group_id = ""

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "verify_pin":
            group_id = request.POST.get("group")
            pin = (request.POST.get("pin") or "").strip()
            pin_group = (
                AttendanceGroup.objects.filter(
                    church=church,
                    is_active=True,
                    district__is_active=True,
                    pk=group_id,
                )
                .select_related("district", "guide", "leader")
                .annotate(active_member_count=Count("members", filter=Q(members__is_active=True), distinct=True))
                .first()
            )
            if pin_group is None:
                messages.error(request, "먼저 교구와 속을 선택해 주세요.")
            elif not pin_group.attendance_pin:
                messages.error(request, "이 속에는 아직 출석 PIN이 설정되어 있지 않습니다.")
            elif pin_group.attendance_pin != pin:
                messages.error(request, "PIN 번호가 맞지 않습니다. 다시 확인해 주세요.")
            else:
                _set_attendance_pin_session(request, pin_group)
                return redirect("attendance:check")
        elif action == "reset_pin_session":
            _clear_attendance_pin_session(request)
            return redirect("attendance:check")

    selected_group = _get_attendance_pin_group(request, church)

    today = timezone.localdate()
    reference_date = today if today.weekday() == 6 else _last_sunday_for(today)
    current_session, _ = AttendanceSession.get_or_create_current(
        church,
        request.user if request.user.is_authenticated else None,
        reference_date=reference_date,
    )

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

    if request.method == "POST" and request.POST.get("action") == "submit_attendance":
        if selected_group is None:
            messages.error(request, "출석을 제출할 속이 선택되지 않았습니다.")
            return redirect("attendance:check")
        if not attendance_check_day:
            messages.error(request, "출석 체크는 주일에만 열립니다. 평일에는 admin 계정으로 강제 공개를 켜야 입력할 수 있습니다.")
            return redirect("attendance:check")

        now = timezone.now()
        records_to_create = []
        updated_count = 0
        for member in members:
            status = _normalize_attendance_status(request.POST.get(f"status_{member.pk}") or AttendanceStatus.PRESENT)
            note = (request.POST.get(f"note_{member.pk}") or "").strip()
            record = record_map.get(member.pk)
            if record is None:
                records_to_create.append(
                    AttendanceRecord(
                        session=current_session,
                        member=member,
                        status=status,
                        note=note,
                        marked_by=request.user if request.user.is_authenticated else None,
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
                if request.user.is_authenticated and record.marked_by_id != request.user.id:
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
                f"{selected_group.district.name} {selected_group.name} 출석을 제출했습니다. ({saved_total}명 반영)",
            )
        else:
            messages.info(request, "변경된 내용이 없어 기존 출석 상태를 그대로 유지했습니다.")
        return redirect("attendance:check")

    member_rows = []
    for member in members:
        record = record_map.get(member.pk)
        member_rows.append(
            {
                "member": member,
                "status": _normalize_attendance_status(record.status) if record else AttendanceStatus.PRESENT,
                "note": record.note if record else "",
            }
        )

    return render(
        request,
        "attendance/check.html",
        {
            "active_church": church,
            "active_attendance_tab": "check",
            "pin_authenticated": selected_group is not None,
            "district_cards": district_cards,
            "selected_district": selected_district,
            "pin_groups": pin_groups,
            "selected_pin_group_id": int(selected_pin_group_id) if selected_pin_group_id else None,
            "selected_group": selected_group,
            "current_session": current_session,
            "member_rows": member_rows,
            "status_choices": CHECK_STATUS_CHOICES,
            "attendance_check_day": attendance_check_day,
            "attendance_test_mode": _is_attendance_test_sunday(request),
            "attendance_force_open": control.force_open,
            "can_force_attendance_open": can_force_attendance_open,
            "show_full_nav": bool(request.user.is_authenticated and _can_access_attendance(request.user)),
            "logout_url": reverse("core:logout") if request.user.is_authenticated else "",
            **_build_church_nav_context(church),
        },
    )


@login_required(login_url="core:login")
def attendance_manual_check_view(request):
    guard = _redirect_attendance_only_user_to_check(request)
    if guard:
        return guard
    if not _can_use_manual_attendance_check(request.user):
        return redirect("attendance:dashboard")

    church = _get_scope_church(request.user)
    today = timezone.localdate()
    reference_date = today if today.weekday() == 6 else _last_sunday_for(today)
    current_session, _ = AttendanceSession.get_or_create_current(church, request.user, reference_date=reference_date)

    district_queryset = (
        AttendanceDistrict.objects.filter(church=church, is_active=True)
        .prefetch_related("groups__members")
        .order_by("sort_order", "name", "id")
    )
    group_queryset = (
        AttendanceGroup.objects.filter(church=church, is_active=True, district__is_active=True)
        .select_related("district", "guide", "leader")
        .annotate(active_member_count=Count("members", filter=Q(members__is_active=True), distinct=True))
        .order_by("district__sort_order", "sort_order", "name", "id")
    )
    groups = list(group_queryset)
    members = list(
        AttendanceMember.objects.filter(group__in=groups, is_active=True)
        .select_related("group", "group__district")
        .order_by("group__district__sort_order", "group__sort_order", "sort_order", "name", "id")
    )

    record_map = {
        record.member_id: record
        for record in AttendanceRecord.objects.filter(session=current_session, member__in=members)
    }

    if request.method == "POST":
        now = timezone.now()
        records_to_create = []
        updated_count = 0
        for member in members:
            status_value = request.POST.get(f"status_{member.pk}")
            note = (request.POST.get(f"note_{member.pk}") or "").strip()
            record = record_map.get(member.pk)

            if not status_value:
                if record is not None and record.note != note:
                    record.note = note
                    record.marked_by = request.user
                    record.marked_at = now
                    record.save(update_fields=["note", "marked_by", "marked_at", "updated_at"])
                    updated_count += 1
                continue

            status = _normalize_attendance_status(status_value)
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
                continue

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
            messages.success(request, f"수동 출석 체크 내용을 저장했습니다. ({saved_total}명 반영)")
        else:
            messages.info(request, "변경된 내용이 없습니다.")
        return redirect("attendance:manual_check")

    member_rows_by_group = {}
    group_summaries = {}
    district_summaries = {}
    for member in members:
        record = record_map.get(member.pk)
        current_status = _normalize_attendance_status(record.status) if record else AttendanceStatus.PRESENT
        is_pending = record is None
        row = {
            "member": member,
            "status": current_status,
            "note": record.note if record else "",
            "is_pending": is_pending,
        }
        member_rows_by_group.setdefault(member.group_id, []).append(row)

        group_summary = group_summaries.setdefault(
            member.group_id,
            {"present": 0, "absent": 0, "total": 0},
        )
        district_summary = district_summaries.setdefault(
            member.group.district_id,
            {"present": 0, "absent": 0, "total": 0},
        )
        for bucket in (group_summary, district_summary):
            bucket["total"] += 1
            if current_status == AttendanceStatus.PRESENT:
                bucket["present"] += 1
            elif current_status == AttendanceStatus.ABSENT:
                bucket["absent"] += 1

    district_sections = []
    groups_by_district = {}
    for group in groups:
        groups_by_district.setdefault(group.district_id, []).append(group)

    for district in district_queryset:
        district_groups = groups_by_district.get(district.id, [])
        if not district_groups:
            continue
        group_sections = []
        for group in district_groups:
            summary = group_summaries.get(group.id, {"present": 0, "absent": 0, "total": 0})
            group_sections.append(
                {
                    "group": group,
                    "summary": summary,
                    "rows": member_rows_by_group.get(group.id, []),
                }
            )
        district_sections.append(
            {
                "district": district,
                "summary": district_summaries.get(district.id, {"present": 0, "absent": 0, "total": 0}),
                "groups": group_sections,
            }
        )

    return render(
        request,
        "attendance/manual_check.html",
        {
            "active_church": church,
            "active_attendance_tab": "manual_check",
            "current_session": current_session,
            "district_sections": district_sections,
            "status_choices": CHECK_STATUS_CHOICES,
            "can_manage_attendance": _is_pastor_or_admin(request.user) or _has_attendance_manage_override(request.user),
            "can_manual_check_attendance": True,
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


def attendance_seed_demo_view(request):
    if not settings.DEBUG:
        raise Http404()

    summaries = _seed_demo_attendance_data()
    if request.GET.get("format") == "text":
        return HttpResponse("\n".join(summaries), content_type="text/plain; charset=utf-8")

    messages.success(request, "테스트용 최근 주일 출석 데이터를 전체 속에 채워두었습니다.")
    return redirect("attendance:dashboard")


@login_required(login_url="core:login")
def attendance_reports_view(request):
    guard = _redirect_attendance_only_user_to_check(request)
    if guard:
        return guard
    if not _can_access_attendance(request.user):
        return redirect("attendance:dashboard")

    church = _get_scope_church(request.user)
    role_context = _build_attendance_role_context(request.user, church)
    all_sessions = [session for session in AttendanceSession.objects.filter(church=church).order_by("-worship_date", "-id") if session.worship_date.weekday() == 6]
    selected_session = all_sessions[0] if all_sessions else None
    session_id = request.GET.get("session")
    if session_id:
        try:
            selected_session = next(session for session in all_sessions if session.pk == int(session_id))
        except (StopIteration, ValueError):
            pass

    group_queryset = _scoped_group_queryset(church, role_context)
    available_districts = (
        AttendanceDistrict.objects.filter(church=church, groups__in=group_queryset)
        .distinct()
        .order_by("sort_order", "name")
    )
    selected_district = None
    district_id = request.GET.get("district")
    if district_id:
        try:
            selected_district = available_districts.get(pk=int(district_id))
            group_queryset = group_queryset.filter(district=selected_district)
        except (AttendanceDistrict.DoesNotExist, ValueError):
            selected_district = None

    selected_report = request.GET.get("report")
    if selected_report not in {"absent", "rate"}:
        selected_report = None

    selected_report = request.GET.get("report")
    if selected_report not in {"absent", "rate"}:
        selected_report = None

    available_groups = group_queryset.order_by("district__sort_order", "sort_order", "name")
    selected_group = None
    group_id = request.GET.get("group")
    if group_id:
        try:
            selected_group = available_groups.get(pk=int(group_id))
        except (AttendanceGroup.DoesNotExist, ValueError):
            selected_group = None

    report_rows = []
    member_rows = []
    detail_title = ""
    if selected_session:
        rows_queryset = (
            AttendanceRecord.objects.filter(session=selected_session, member__group__in=group_queryset)
            .values("member__group__district__name", "member__group__name", "member__group_id", "status")
            .annotate(total=Count("id"))
            .order_by("member__group__district__name", "member__group__name")
        )
        buckets = {}
        for row in rows_queryset:
            key = (row["member__group__district__name"], row["member__group__name"], row["member__group_id"])
            bucket = buckets.setdefault(
                key,
                {
                    "district_name": row["member__group__district__name"],
                    "group_name": row["member__group__name"],
                    "group_id": row["member__group_id"],
                    "present": 0,
                    "absent": 0,
                    "total": 0,
                },
            )
            if row["status"] == AttendanceStatus.PRESENT:
                bucket["present"] += row["total"]
            else:
                bucket["absent"] += row["total"]
            bucket["total"] += row["total"]
        report_rows = list(buckets.values())

        if selected_group:
            detail_members = AttendanceMember.objects.filter(group=selected_group, is_active=True).order_by("name")
            detail_title = f"{selected_group.name} 속원별 출석 상태"
        elif selected_district:
            detail_members = AttendanceMember.objects.filter(
                group__district=selected_district,
                group__in=group_queryset,
                is_active=True,
            ).select_related("group", "group__district").order_by("group__sort_order", "group__name", "name")
            detail_title = f"{selected_district.name} 교구 전체 출석 상태"
        else:
            detail_members = AttendanceMember.objects.filter(
                group__in=group_queryset,
                is_active=True,
            ).select_related("group", "group__district").order_by("group__district__sort_order", "group__sort_order", "name")
            detail_title = "전교인 전체 출석 상태"

        detail_members = list(detail_members)
        record_map = {
            record.member_id: record
            for record in AttendanceRecord.objects.filter(
                session=selected_session,
                member__in=detail_members,
            ).select_related("member")
        }
        for member in detail_members:
            record = record_map.get(member.id)
            member_rows.append(
                {
                    "member_name": member.name,
                    "district_name": member.group.district.name,
                    "group_name": member.group.name,
                    "status": _normalize_attendance_status(record.status) if record else AttendanceStatus.ABSENT,
                    "note": record.note if record else "",
                }
            )

    paginator = Paginator(report_rows, 12)
    page_obj = paginator.get_page(request.GET.get("page"))
    member_page_obj = Paginator(member_rows, 40).get_page(request.GET.get("member_page"))

    return render(
        request,
        "attendance/reports.html",
        {
            "active_church": church,
            "active_attendance_tab": "reports",
            "selected_session": selected_session,
            "selected_district": selected_district,
            "selected_group": selected_group,
            "available_districts": available_districts,
            "available_groups": available_groups,
            "member_rows": member_rows,
            "member_page_obj": member_page_obj,
            "detail_title": detail_title,
            "available_sessions": all_sessions[:12],
            "page_obj": page_obj,
            "can_manage_attendance": role_context["has_full_attendance_access"],
            **_build_church_nav_context(church),
        },
    )


@login_required(login_url="core:login")
def attendance_report_hub_view(request):
    guard = _redirect_attendance_only_user_to_check(request)
    if guard:
        return guard
    if not _can_access_attendance(request.user):
        return redirect("attendance:dashboard")

    church = _get_scope_church(request.user)
    role_context = _build_attendance_role_context(request.user, church)
    group_queryset = _scoped_group_queryset(church, role_context)
    available_districts = (
        AttendanceDistrict.objects.filter(church=church, groups__in=group_queryset)
        .distinct()
        .order_by("sort_order", "name")
    )
    all_sessions = [
        session
        for session in AttendanceSession.objects.filter(church=church).order_by("-worship_date", "-id")
        if session.worship_date.weekday() == 6
    ]
    selected_session = all_sessions[0] if all_sessions else None
    session_id = request.GET.get("session")
    if session_id:
        try:
            selected_session = next(session for session in all_sessions if session.pk == int(session_id))
        except (StopIteration, ValueError):
            pass

    selected_district = None
    district_id = request.GET.get("district")
    if district_id:
        try:
            selected_district = available_districts.get(pk=int(district_id))
            group_queryset = group_queryset.filter(district=selected_district)
        except (AttendanceDistrict.DoesNotExist, ValueError):
            selected_district = None

    available_groups = group_queryset.order_by("district__sort_order", "sort_order", "name")
    selected_group = None
    group_id = request.GET.get("group")
    if group_id:
        try:
            selected_group = available_groups.get(pk=int(group_id))
        except (AttendanceGroup.DoesNotExist, ValueError):
            selected_group = None

    selected_report = request.GET.get("report")
    if selected_report not in {"weekly", "absent", "streak", "personal"}:
        selected_report = None

    absent_rows = []
    streak_rows = []
    weekly_rows = []
    weekly_member_rows = []
    weekly_member_sections = []
    weekly_summary = None
    personal_member = None
    personal_rows = []
    personal_last_present = None
    personal_current_streak = 0
    member_search = request.GET.get("member_search", "").strip()
    personal_member_options = []
    personal_search_matches = []
    min_week_options = [2, 3, 4, 5]
    selected_min_weeks = 2
    min_weeks_param = request.GET.get("min_weeks")
    if min_weeks_param:
        try:
            selected_min_weeks = max(2, int(min_weeks_param))
        except ValueError:
            selected_min_weeks = 2
    if selected_min_weeks not in min_week_options:
        selected_min_weeks = 5 if selected_min_weeks > 5 else 2

    available_members = list(
        AttendanceMember.objects.filter(group__in=group_queryset, is_active=True)
        .select_related("group", "group__district")
        .order_by("group__district__sort_order", "group__sort_order", "name")
    )
    personal_member_options = available_members
    if selected_report == "personal" and selected_group:
        personal_member_options = [member for member in personal_member_options if member.group_id == selected_group.id]
    if selected_report == "personal" and member_search:
        personal_search_matches = [
            member
            for member in personal_member_options
            if member_search.lower() in member.name.lower()
        ]
        if not request.GET.get("member") and len(personal_search_matches) == 1:
            personal_member = personal_search_matches[0]
    member_id = request.GET.get("member")
    if member_id:
        try:
            personal_member = next(member for member in personal_member_options if member.id == int(member_id))
        except (StopIteration, ValueError):
            personal_member = None

    if selected_session:
        members = available_members
        record_map = {
            record.member_id: record
            for record in AttendanceRecord.objects.filter(
                session=selected_session,
                member__group__in=group_queryset,
            ).select_related("member", "member__group", "member__group__district")
        }
        for member in members:
            record = record_map.get(member.id)
            if record and _normalize_attendance_status(record.status) == AttendanceStatus.ABSENT:
                absent_rows.append(
                    {
                        "district_name": member.group.district.name,
                        "group_name": member.group.name,
                        "member_name": member.name,
                    }
                )

        district_buckets = {}
        group_buckets = {}
        for member in members:
            record = record_map.get(member.id)
            status = _normalize_attendance_status(record.status) if record else None

            district_bucket = district_buckets.setdefault(
                member.group.district_id,
                {
                    "id": member.group.district_id,
                    "name": member.group.district.name,
                    "sort_order": member.group.district.sort_order,
                    "present": 0,
                    "absent": 0,
                    "pending": 0,
                    "total": 0,
                },
            )
            group_bucket = group_buckets.setdefault(
                member.group_id,
                {
                    "id": member.group_id,
                    "name": member.group.name,
                    "district_name": member.group.district.name,
                    "sort_order": member.group.sort_order,
                    "present": 0,
                    "absent": 0,
                    "pending": 0,
                    "total": 0,
                },
            )

            for bucket in (district_bucket, group_bucket):
                bucket["total"] += 1
                if status == AttendanceStatus.PRESENT:
                    bucket["present"] += 1
                elif status == AttendanceStatus.ABSENT:
                    bucket["absent"] += 1
                else:
                    bucket["pending"] += 1

        if selected_district:
            weekly_rows = sorted(group_buckets.values(), key=lambda row: (row["sort_order"], row["name"]))
        else:
            weekly_rows = sorted(district_buckets.values(), key=lambda row: (row["sort_order"], row["name"]))

        for row in weekly_rows:
            row["attendance_rate"] = round((row["present"] / row["total"]) * 100, 1) if row["total"] else 0

        if selected_group:
            selected_group_members = [member for member in members if member.group_id == selected_group.id]
            total_count = len(selected_group_members)
            present_count = 0
            absent_count = 0
            pending_count = 0
            for member in selected_group_members:
                record = record_map.get(member.id)
                status = _normalize_attendance_status(record.status) if record else None
                if status == AttendanceStatus.PRESENT:
                    present_count += 1
                elif status == AttendanceStatus.ABSENT:
                    absent_count += 1
                else:
                    pending_count += 1
            weekly_summary = {
                "present": present_count,
                "absent": absent_count,
                "pending": pending_count,
                "total": total_count,
                "attendance_rate": round((present_count / total_count) * 100, 1) if total_count else 0,
            }
        elif selected_district:
            district_row = next((row for row in district_buckets.values() if row["id"] == selected_district.id), None)
            if district_row:
                weekly_summary = {
                    "present": district_row["present"],
                    "absent": district_row["absent"],
                    "pending": district_row["pending"],
                    "total": district_row["total"],
                    "attendance_rate": round((district_row["present"] / district_row["total"]) * 100, 1) if district_row["total"] else 0,
                }
        else:
            total_count = len(members)
            present_count = 0
            absent_count = 0
            pending_count = 0
            for member in members:
                record = record_map.get(member.id)
                status = _normalize_attendance_status(record.status) if record else None
                if status == AttendanceStatus.PRESENT:
                    present_count += 1
                elif status == AttendanceStatus.ABSENT:
                    absent_count += 1
                else:
                    pending_count += 1
            weekly_summary = {
                "present": present_count,
                "absent": absent_count,
                "pending": pending_count,
                "total": total_count,
                "attendance_rate": round((present_count / total_count) * 100, 1) if total_count else 0,
            }

        detail_members = members
        if selected_group:
            detail_members = [member for member in members if member.group_id == selected_group.id]

        for member in detail_members:
            record = record_map.get(member.id)
            weekly_member_rows.append(
                {
                    "member_name": member.name,
                    "district_name": member.group.district.name,
                    "group_name": member.group.name,
                    "status": _normalize_attendance_status(record.status) if record else AttendanceStatus.ABSENT,
                }
            )

        weekly_member_sections = []
        if selected_group:
            weekly_member_sections = [
                {
                    "title": selected_group.name,
                    "subtitle": selected_group.district.name,
                    "rows": weekly_member_rows,
                }
            ]
        elif selected_district:
            grouped_rows = {}
            for row in weekly_member_rows:
                grouped_rows.setdefault(row["group_name"], []).append(row)
            weekly_member_sections = [
                {
                    "title": group_name,
                    "subtitle": selected_district.name,
                    "rows": rows,
                }
                for group_name, rows in sorted(grouped_rows.items(), key=lambda item: item[0])
            ]
        else:
            grouped_rows = {}
            for row in weekly_member_rows:
                district_bucket = grouped_rows.setdefault(row["district_name"], {})
                district_bucket.setdefault(row["group_name"], []).append(row)
            for district_name, group_map in sorted(grouped_rows.items(), key=lambda item: item[0]):
                for group_name, rows in sorted(group_map.items(), key=lambda item: item[0]):
                    weekly_member_sections.append(
                        {
                            "title": district_name,
                            "subtitle": group_name,
                            "rows": rows,
                        }
                    )

        session_ids = []
        if selected_session:
            start_collecting = False
            for session in all_sessions:
                if session.id == selected_session.id:
                    start_collecting = True
                if start_collecting:
                    session_ids.append(session.id)
        if session_ids:
            streak_record_map = {}
            for record in AttendanceRecord.objects.filter(
                session_id__in=session_ids,
                member__group__in=group_queryset,
            ).select_related("member", "member__group", "member__group__district"):
                streak_record_map[(record.member_id, record.session_id)] = _normalize_attendance_status(record.status)

            for member in members:
                streak_weeks = 0
                for session_id in session_ids:
                    status = streak_record_map.get((member.id, session_id))
                    if status == AttendanceStatus.ABSENT:
                        streak_weeks += 1
                        continue
                    break
                matches_streak = (
                    streak_weeks >= 5 if selected_min_weeks == 5 else streak_weeks == selected_min_weeks
                )
                if matches_streak:
                    streak_rows.append(
                        {
                            "district_name": member.group.district.name,
                            "group_name": member.group.name,
                            "member_name": member.name,
                            "streak_weeks": streak_weeks,
                        }
                    )

        if personal_member:
            recent_sessions = all_sessions[:8]
            personal_record_map = {
                record.session_id: _normalize_attendance_status(record.status)
                for record in AttendanceRecord.objects.filter(
                    session__in=recent_sessions,
                    member=personal_member,
                )
            }
            for session in recent_sessions:
                status = personal_record_map.get(session.id)
                normalized = status if status else AttendanceStatus.ABSENT
                personal_rows.append(
                    {
                        "worship_date": session.worship_date,
                        "status": normalized,
                    }
                )
                if normalized == AttendanceStatus.PRESENT and personal_last_present is None:
                    personal_last_present = session.worship_date

            for session in all_sessions:
                status = personal_record_map.get(session.id)
                if status == AttendanceStatus.ABSENT:
                    personal_current_streak += 1
                    continue
                break

    return render(
        request,
        "attendance/report_hub.html",
        {
            "active_church": church,
            "active_attendance_tab": "report_hub",
            "selected_session": selected_session,
            "available_sessions": all_sessions,
            "available_districts": available_districts,
            "available_groups": available_groups,
            "available_members": available_members,
            "personal_member_options": personal_member_options,
            "personal_search_matches": personal_search_matches,
            "selected_district": selected_district,
            "selected_group": selected_group,
            "selected_report": selected_report,
            "absent_rows": absent_rows,
            "absent_count": len(absent_rows),
            "streak_rows": streak_rows,
            "weekly_rows": weekly_rows,
            "weekly_summary": weekly_summary,
            "weekly_member_rows": weekly_member_rows,
            "weekly_member_sections": weekly_member_sections,
            "personal_member": personal_member,
            "personal_rows": personal_rows,
            "personal_last_present": personal_last_present,
            "personal_current_streak": personal_current_streak,
            "member_search": member_search,
            "min_week_options": min_week_options,
            "selected_min_weeks": selected_min_weeks,
            "can_manage_attendance": role_context["has_full_attendance_access"],
            **_build_church_nav_context(church),
        },
    )


@login_required(login_url="core:login")
def attendance_weekly_pdf_view(request):
    guard = _redirect_attendance_only_user_to_check(request)
    if guard:
        return guard
    if not _can_access_attendance(request.user):
        return redirect("attendance:dashboard")

    church = _get_scope_church(request.user)
    role_context = _build_attendance_role_context(request.user, church)
    group_queryset = _scoped_group_queryset(church, role_context)
    available_districts = (
        AttendanceDistrict.objects.filter(church=church, groups__in=group_queryset)
        .distinct()
        .order_by("sort_order", "name")
    )
    all_sessions = [
        session
        for session in AttendanceSession.objects.filter(church=church).order_by("-worship_date", "-id")
        if session.worship_date.weekday() == 6
    ]
    selected_session = all_sessions[0] if all_sessions else None
    session_id = request.GET.get("session")
    if session_id:
        try:
            selected_session = next(session for session in all_sessions if session.pk == int(session_id))
        except (StopIteration, ValueError):
            pass

    if selected_session is None:
        raise Http404("출석 세션이 없습니다.")

    selected_district = None
    district_id = request.GET.get("district")
    if district_id:
        try:
            selected_district = available_districts.get(pk=int(district_id))
            group_queryset = group_queryset.filter(district=selected_district)
        except (AttendanceDistrict.DoesNotExist, ValueError):
            selected_district = None

    available_groups = group_queryset.order_by("district__sort_order", "sort_order", "name")
    selected_group = None
    group_id = request.GET.get("group")
    if group_id:
        try:
            selected_group = available_groups.get(pk=int(group_id))
        except (AttendanceGroup.DoesNotExist, ValueError):
            selected_group = None

    sections = _build_weekly_pdf_sections(
        group_queryset,
        selected_session,
        selected_district=selected_district,
        selected_group=selected_group,
    )

    if selected_group is not None:
        report_title = f"{church.name} {selected_group.name} 출석표"
    elif selected_district is not None:
        report_title = f"{church.name} {selected_district.name} 교구 출석표"
    else:
        report_title = f"{church.name} 전체 출석표"

    safe_title = report_title.replace(" ", "_")
    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{safe_title}_{selected_session.worship_date}.pdf"'

    _ensure_pdf_font()
    page_width, page_height = landscape(A4)
    pdf = canvas.Canvas(response, pagesize=landscape(A4))
    pdf.setTitle(report_title)

    if not sections:
        _draw_attendance_pdf_header(
            pdf,
            page_width,
            page_height,
            report_title,
            f"{selected_session.worship_date:%Y-%m-%d} 주일",
        )
        pdf.setFont(_PDF_FONT_NAME, 12)
        pdf.setFillColor(colors.HexColor("#6f5e4f"))
        pdf.drawString(24, page_height - 80, "출력할 출석 데이터가 없습니다.")
        pdf.showPage()
        pdf.save()
        return response

    margin = 24
    gutter = 12
    columns = 3
    column_width = (page_width - (margin * 2) - (gutter * (columns - 1))) / columns
    column_x = [margin + ((column_width + gutter) * idx) for idx in range(columns)]
    column_y = [page_height - 56 for _ in range(columns)]
    subtitle = f"{selected_session.worship_date:%Y-%m-%d} 주일"
    _draw_attendance_pdf_header(pdf, page_width, page_height, report_title, subtitle)
    page_number = 1

    for section in sections:
        box_height = 50 + (max(1, ceil(len(section["members"]) / 2)) * 15)

        target_column = None
        for idx in range(columns):
            if column_y[idx] - box_height >= margin:
                target_column = idx
                break

        if target_column is None:
            _draw_attendance_pdf_footer(pdf, page_width, page_number)
            pdf.showPage()
            _draw_attendance_pdf_header(pdf, page_width, page_height, report_title, subtitle)
            column_y = [page_height - 56 for _ in range(columns)]
            page_number += 1
            target_column = 0

        used_height = _draw_attendance_pdf_section_v2(
            pdf,
            column_x[target_column],
            column_y[target_column],
            column_width,
            section,
        )
        column_y[target_column] -= used_height + 10

    _draw_attendance_pdf_footer(pdf, page_width, page_number)
    pdf.showPage()
    pdf.save()
    return response


@login_required(login_url="core:login")
def attendance_weekly_pdf_email_view(request):
    guard = _redirect_attendance_only_user_to_check(request)
    if guard:
        return guard
    if not _can_access_attendance(request.user):
        return redirect("attendance:dashboard")

    church = _get_scope_church(request.user)
    role_context = _build_attendance_role_context(request.user, church)
    group_queryset = _scoped_group_queryset(church, role_context)
    available_districts = (
        AttendanceDistrict.objects.filter(church=church, groups__in=group_queryset)
        .distinct()
        .order_by("sort_order", "name")
    )
    all_sessions = [
        session
        for session in AttendanceSession.objects.filter(church=church).order_by("-worship_date", "-id")
        if session.worship_date.weekday() == 6
    ]
    selected_session = all_sessions[0] if all_sessions else None
    session_id = request.GET.get("session")
    if session_id:
        try:
            selected_session = next(session for session in all_sessions if session.pk == int(session_id))
        except (StopIteration, ValueError):
            pass

    if selected_session is None:
        messages.error(request, "출석 세션이 없습니다.")
        return redirect("attendance:report_hub")

    if not request.user.email:
        messages.error(request, "로그인한 계정에 이메일 주소가 없습니다.")
        return redirect(f"{reverse('attendance:report_hub')}?report=weekly&session={selected_session.id}")

    selected_district = None
    district_id = request.GET.get("district")
    if district_id:
        try:
            selected_district = available_districts.get(pk=int(district_id))
            group_queryset = group_queryset.filter(district=selected_district)
        except (AttendanceDistrict.DoesNotExist, ValueError):
            selected_district = None

    available_groups = group_queryset.order_by("district__sort_order", "sort_order", "name")
    selected_group = None
    group_id = request.GET.get("group")
    if group_id:
        try:
            selected_group = available_groups.get(pk=int(group_id))
        except (AttendanceGroup.DoesNotExist, ValueError):
            selected_group = None

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "") or getattr(settings, "EMAIL_HOST_USER", "")
    if not from_email:
        messages.error(request, "발신 이메일 설정이 없습니다. DEFAULT_FROM_EMAIL 또는 EMAIL_HOST_USER를 설정해 주세요.")
        return redirect(f"{reverse('attendance:report_hub')}?report=weekly&session={selected_session.id}")

    pdf_bytes, safe_title = _build_weekly_pdf_document(
        church,
        selected_session,
        group_queryset,
        selected_district=selected_district,
        selected_group=selected_group,
    )

    scope_label = "전교인 전체"
    if selected_group:
        scope_label = f"{selected_group.district.name} {selected_group.name}"
    elif selected_district:
        scope_label = f"{selected_district.name} 교구"

    message = EmailMessage(
        subject=f"[{church.name}] {selected_session.worship_date:%Y-%m-%d} 주일 출석 현황",
        body=(
            f"{church.name} {selected_session.worship_date:%Y-%m-%d} 주일 출석 현황 PDF를 보내드립니다.\n\n"
            f"조회 범위: {scope_label}\n"
            f"첨부 파일을 확인해 주세요."
        ),
        from_email=from_email,
        to=[request.user.email],
    )
    message.attach(
        f"{safe_title}_{selected_session.worship_date}.pdf",
        pdf_bytes,
        "application/pdf",
    )
    message.send(fail_silently=False)

    redirect_url = f"{reverse('attendance:report_hub')}?report=weekly&session={selected_session.id}"
    if selected_district:
        redirect_url += f"&district={selected_district.id}"
    if selected_group:
        redirect_url += f"&group={selected_group.id}"
    messages.success(request, f"{request.user.email} 로 출석표 PDF를 보냈습니다.")
    return redirect(redirect_url)


@login_required(login_url="core:login")
def attendance_manage_view(request):
    guard = _redirect_attendance_only_user_to_check(request)
    if guard:
        return guard
    guard = _ensure_manage_attendance(request)
    if guard:
        return guard

    church = _get_scope_church(request.user)
    district_form = AttendanceDistrictForm(request.POST or None)
    if request.method == "POST" and request.POST.get("action") == "add_district":
        if district_form.is_valid():
            district = district_form.save(commit=False)
            district.name = (district.name or "").strip()
            district.church = church
            if AttendanceDistrict.objects.filter(church=church, name=district.name).exists():
                district_form.add_error("name", "같은 이름의 교구가 이미 있습니다.")
            else:
                next_sort = (
                    AttendanceDistrict.objects.filter(church=church).aggregate(max_sort=Max("sort_order")).get("max_sort")
                    or 0
                )
                district.sort_order = next_sort + 1
                district.save()
                if district.name == "교구장":
                    AttendanceGroup.objects.get_or_create(
                        church=church,
                        district=district,
                        name="교구장",
                        defaults={"sort_order": 1, "is_active": True},
                    )
                messages.success(request, "교구를 추가했습니다.")
                return redirect("attendance:manage_district", district_id=district.id)

    district_count = AttendanceDistrict.objects.filter(church=church).exclude(name="교구장").count()
    group_count = AttendanceGroup.objects.filter(church=church).exclude(district__name="교구장").count()
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
        .select_related("district", "guide", "leader")
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
            "district_form": district_form,
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
    district = (
        AttendanceDistrict.objects.filter(church=church, pk=district_id)
        .prefetch_related("leaders")
        .annotate(member_total=Count("groups__members", distinct=True))
        .first()
    )
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
            group_form = AttendanceGroupCreateForm(request.POST)
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
    add_group_form = AttendanceGroupCreateForm()
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
def attendance_district_manage_view(request, district_id):
    guard = _ensure_manage_attendance(request)
    if guard:
        return guard

    church = _get_scope_church(request.user)
    district = (
        AttendanceDistrict.objects.filter(church=church, pk=district_id)
        .prefetch_related("leaders")
        .annotate(member_total=Count("groups__members", distinct=True))
        .first()
    )
    if district is None:
        messages.error(request, "선택한 교구를 찾을 수 없습니다.")
        return redirect("attendance:manage")

    existing_leaders = list(district.leaders.all().order_by("id")[:2])

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update_district":
            leader_forms = []
            for index in range(2):
                instance = existing_leaders[index] if index < len(existing_leaders) else None
                leader_forms.append(
                    AttendanceDistrictLeaderForm(
                        request.POST,
                        instance=instance,
                        church=church,
                        prefix=f"leader-{index}",
                    )
                )
            if all(form.is_valid() for form in leader_forms):
                kept_ids = []
                for form in leader_forms:
                    name = (form.cleaned_data.get("name") or "").strip()
                    linked_user = form.cleaned_data.get("linked_user")
                    instance = form.instance if getattr(form.instance, "pk", None) else None
                    if not name and not linked_user:
                        if instance and instance.pk:
                            instance.delete()
                        continue
                    leader = form.save(commit=False)
                    leader.district = district
                    leader.save()
                    kept_ids.append(leader.pk)
                AttendanceDistrictLeader.objects.filter(district=district).exclude(pk__in=kept_ids).delete()
                messages.success(request, "교구장을 저장했습니다.")
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

    leader_forms = []
    for index in range(2):
        instance = existing_leaders[index] if index < len(existing_leaders) else None
        leader_forms.append(
            AttendanceDistrictLeaderForm(
                instance=instance,
                church=church,
                prefix=f"leader-{index}",
            )
        )
    add_group_form = AttendanceGroupForm(district=district)
    groups = list(
        AttendanceGroup.objects.filter(district=district)
        .select_related("guide", "leader")
        .annotate(member_total=Count("members", distinct=True))
        .order_by("sort_order", "name")
    )

    return render(
        request,
        "attendance/manage_district.html",
        {
            "active_church": church,
            "active_attendance_tab": "manage",
            "district": district,
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
        .select_related("district", "guide", "leader")
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


@login_required(login_url="core:login")
def attendance_district_manage_view(request, district_id):
    guard = _ensure_manage_attendance(request)
    if guard:
        return guard

    church = _get_scope_church(request.user)
    district = (
        AttendanceDistrict.objects.filter(church=church, pk=district_id)
        .prefetch_related("leaders")
        .annotate(member_total=Count("groups__members", distinct=True))
        .first()
    )
    if district is None:
        messages.error(request, "선택한 교구를 찾을 수 없습니다.")
        return redirect("attendance:manage")

    existing_leaders = list(district.leaders.all().order_by("id")[:2])

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "update_district":
            leader_forms = []
            for index in range(2):
                instance = existing_leaders[index] if index < len(existing_leaders) else None
                leader_forms.append(
                    AttendanceDistrictLeaderForm(
                        request.POST,
                        instance=instance,
                        church=church,
                        prefix=f"leader-{index}",
                    )
                )
            if all(form.is_valid() for form in leader_forms):
                kept_ids = []
                for form in leader_forms:
                    name = (form.cleaned_data.get("name") or "").strip()
                    linked_user = form.cleaned_data.get("linked_user")
                    instance = form.instance if getattr(form.instance, "pk", None) else None
                    if not name and not linked_user:
                        if instance and instance.pk:
                            instance.delete()
                        continue
                    leader = form.save(commit=False)
                    leader.district = district
                    leader.save()
                    kept_ids.append(leader.pk)
                AttendanceDistrictLeader.objects.filter(district=district).exclude(pk__in=kept_ids).delete()
                messages.success(request, "교구장을 저장했습니다.")
                return redirect("attendance:manage_district", district_id=district.id)
        elif action == "add_group":
            group_form = AttendanceGroupCreateForm(request.POST)
            if group_form.is_valid():
                group = group_form.save(commit=False)
                group.church = church
                group.district = district
                group.save()
                messages.success(request, "속을 추가했습니다.")
                return redirect("attendance:manage_district", district_id=district.id)

    leader_forms = []
    for index in range(2):
        instance = existing_leaders[index] if index < len(existing_leaders) else None
        leader_forms.append(
            AttendanceDistrictLeaderForm(
                instance=instance,
                church=church,
                prefix=f"leader-{index}",
            )
        )

    add_group_form = AttendanceGroupCreateForm()
    groups = list(
        AttendanceGroup.objects.filter(district=district)
        .select_related("guide", "leader")
        .annotate(member_total=Count("members", distinct=True))
        .order_by("sort_order", "name")
    )

    return render(
        request,
        "attendance/manage_district.html",
        {
            "active_church": church,
            "active_attendance_tab": "manage",
            "district": district,
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
        .select_related("district", "guide", "leader")
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
        elif action == "save_members":
            members = list(AttendanceMember.objects.filter(group=group).order_by("sort_order", "name", "id"))
            member_forms = [
                (member, AttendanceMemberForm(request.POST, instance=member, church=church, prefix=f"member-{member.id}"))
                for member in members
            ]
            if all(form.is_valid() for _, form in member_forms):
                for _, form in member_forms:
                    form.save()
                messages.success(request, "속원 정보를 한 번에 저장했습니다.")
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
    member_forms = [
        (member, AttendanceMemberForm(instance=member, church=church, prefix=f"member-{member.id}"))
        for member in members
    ]

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


@login_required(login_url="core:login")
def attendance_group_manage_view(request, group_id):
    guard = _ensure_manage_attendance(request)
    if guard:
        return guard

    church = _get_scope_church(request.user)
    group = (
        AttendanceGroup.objects.filter(church=church, pk=group_id)
        .select_related("district", "guide", "leader")
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
        elif action == "save_members":
            members = list(AttendanceMember.objects.filter(group=group).order_by("sort_order", "name", "id"))
            member_forms = [
                (member, AttendanceMemberForm(request.POST, instance=member, church=church, prefix=f"member-{member.id}"))
                for member in members
            ]
            if all(form.is_valid() for _, form in member_forms):
                for _, form in member_forms:
                    form.save()
                messages.success(request, "속원 정보를 한 번에 저장했습니다.")
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
    member_forms = [
        (member, AttendanceMemberForm(instance=member, church=church, prefix=f"member-{member.id}"))
        for member in members
    ]

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
