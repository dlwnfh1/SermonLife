from django import forms
from django.contrib.auth import get_user_model

from .models import (
    AttendanceDistrict,
    AttendanceDistrictLeader,
    AttendanceGroup,
    AttendanceMember,
    AttendanceSmallGroupReport,
)


User = get_user_model()


class AttendanceDistrictForm(forms.ModelForm):
    class Meta:
        model = AttendanceDistrict
        fields = ["name"]
        labels = {
            "name": "교구 이름",
        }


class AttendanceDistrictLeaderForm(forms.ModelForm):
    class Meta:
        model = AttendanceDistrictLeader
        fields = ["name", "linked_user"]
        labels = {
            "name": "교구장 이름",
            "linked_user": "링크된 유저",
        }

    def __init__(self, *args, church=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = User.objects.all().order_by("username")
        if church is not None:
            queryset = queryset.filter(userprofile__church=church).order_by("username")
        self.fields["linked_user"].queryset = queryset
        self.fields["linked_user"].required = False
        self.fields["name"].required = False


class AttendanceMemberChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return obj.name


class AttendanceGroupForm(forms.ModelForm):
    guide = AttendanceMemberChoiceField(
        queryset=AttendanceMember.objects.none(),
        required=False,
        label="인도자",
    )
    leader = AttendanceMemberChoiceField(
        queryset=AttendanceMember.objects.none(),
        required=False,
        label="속장",
    )

    class Meta:
        model = AttendanceGroup
        fields = ["guide", "leader", "attendance_pin"]
        labels = {
            "attendance_pin": "출석 PIN",
        }

    def __init__(self, *args, group=None, district=None, **kwargs):
        super().__init__(*args, **kwargs)
        target_group = group or (self.instance if getattr(self.instance, "pk", None) else None)
        target_district = district or (target_group.district if target_group else None)

        member_queryset = AttendanceMember.objects.none()
        if target_group:
            member_queryset = AttendanceMember.objects.filter(group=target_group, is_active=True).order_by(
                "sort_order",
                "name",
                "id",
            )
        self.fields["guide"].queryset = member_queryset
        self.fields["leader"].queryset = member_queryset
        self.fields["attendance_pin"].required = False
        self.fields["attendance_pin"].widget.attrs.update(
            {
                "inputmode": "numeric",
                "pattern": r"\d{5}",
                "maxlength": "5",
                "placeholder": "예: 13579",
            }
        )

    def clean_attendance_pin(self):
        pin = (self.cleaned_data.get("attendance_pin") or "").strip()
        if pin and (not pin.isdigit() or len(pin) != 5):
            raise forms.ValidationError("출석 PIN은 5자리 숫자로 입력해 주세요.")
        return pin


class AttendanceGroupCreateForm(forms.ModelForm):
    class Meta:
        model = AttendanceGroup
        fields = ["name"]
        labels = {
            "name": "속 이름",
        }


class AttendanceMemberForm(forms.ModelForm):
    class Meta:
        model = AttendanceMember
        fields = ["name", "linked_user", "phone"]
        labels = {
            "name": "속원 이름",
            "linked_user": "링크된 유저",
            "phone": "연락처",
        }

    def __init__(self, *args, church=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = User.objects.all().order_by("username")
        if church is not None:
            queryset = queryset.filter(userprofile__church=church).order_by("username")
        self.fields["linked_user"].queryset = queryset
        self.fields["linked_user"].required = False


class AttendanceSmallGroupReportForm(forms.ModelForm):
    absent_members = forms.ModelMultipleChoiceField(
        queryset=AttendanceMember.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="결석자 명단",
    )

    class Meta:
        model = AttendanceSmallGroupReport
        fields = [
            "meeting_date",
            "place",
            "attendee_count",
            "offering_amount",
            "absent_members",
            "next_meeting_place",
            "special_notes",
        ]
        labels = {
            "meeting_date": "속회 모임 날짜",
            "place": "장소",
            "attendee_count": "참석자 수",
            "offering_amount": "속회 헌금 총액",
            "next_meeting_place": "다음 속회 모임 장소",
            "special_notes": "특이사항",
        }
        widgets = {
            "meeting_date": forms.DateInput(attrs={"type": "date"}),
            "place": forms.TextInput(attrs={"placeholder": "예: 김집사님 댁"}),
            "attendee_count": forms.NumberInput(attrs={"min": "0"}),
            "offering_amount": forms.NumberInput(attrs={"min": "0", "step": "0.01"}),
            "next_meeting_place": forms.TextInput(attrs={"placeholder": "예: 박권사님 댁"}),
            "special_notes": forms.Textarea(attrs={"rows": 4, "placeholder": "필요한 내용이 있으면 적어 주세요."}),
        }

    def __init__(self, *args, group=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group
        member_queryset = AttendanceMember.objects.none()
        if group is not None:
            member_queryset = AttendanceMember.objects.filter(group=group, is_active=True).order_by(
                "sort_order",
                "name",
                "id",
            )
        self.fields["absent_members"].queryset = member_queryset
        if not self.instance.pk and group is not None:
            self.fields["attendee_count"].initial = member_queryset.count()
            self.fields["offering_amount"].initial = ""

    def clean_attendee_count(self):
        attendee_count = self.cleaned_data.get("attendee_count") or 0
        if attendee_count < 0:
            raise forms.ValidationError("참석자 수는 0명 이상으로 입력해 주세요.")
        return attendee_count

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.group is not None:
            instance.group = self.group
            instance.church = self.group.church
        if commit:
            instance.save()
            self.save_m2m()
        return instance
