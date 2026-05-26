from django import forms
from django.contrib.auth import get_user_model

from .models import AttendanceDistrict, AttendanceDistrictLeader, AttendanceGroup, AttendanceMember


User = get_user_model()


class AttendanceDistrictForm(forms.ModelForm):
    class Meta:
        model = AttendanceDistrict
        fields = ["name", "sort_order", "is_active"]
        labels = {
            "name": "교구 이름",
            "sort_order": "정렬 순서",
            "is_active": "사용 중",
        }


class AttendanceDistrictLeaderForm(forms.ModelForm):
    class Meta:
        model = AttendanceDistrictLeader
        fields = ["name", "linked_user", "is_primary"]
        labels = {
            "name": "교구장 이름",
            "linked_user": "링크된 유저",
            "is_primary": "대표 교구장",
        }

    def __init__(self, *args, church=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = User.objects.all().order_by("username")
        if church is not None:
            queryset = queryset.filter(userprofile__church=church).order_by("username")
        self.fields["linked_user"].queryset = queryset
        self.fields["linked_user"].required = False


class AttendanceLeaderChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        if obj.linked_user:
            return f"{obj.name} ({obj.linked_user.username}: 링크된 유저)"
        return f"{obj.name} (링크된 유저 없음)"


class AttendanceGroupForm(forms.ModelForm):
    leader = AttendanceLeaderChoiceField(
        queryset=AttendanceMember.objects.none(),
        required=False,
        label="속장",
    )

    class Meta:
        model = AttendanceGroup
        fields = ["name", "leader", "sort_order", "is_active"]
        labels = {
            "name": "속 이름",
            "sort_order": "정렬 순서",
            "is_active": "사용 중",
        }

    def __init__(self, *args, group=None, district=None, **kwargs):
        super().__init__(*args, **kwargs)
        target_group = group or (self.instance if getattr(self.instance, "pk", None) else None)
        leader_queryset = AttendanceMember.objects.none()
        if target_group:
            leader_queryset = AttendanceMember.objects.filter(group=target_group, is_active=True).order_by(
                "sort_order", "name"
            )
        self.fields["leader"].queryset = leader_queryset


class AttendanceMemberForm(forms.ModelForm):
    class Meta:
        model = AttendanceMember
        fields = ["name", "linked_user", "phone", "note", "sort_order", "is_active"]
        labels = {
            "name": "속원 이름",
            "linked_user": "링크된 유저",
            "phone": "연락처",
            "note": "메모",
            "sort_order": "정렬 순서",
            "is_active": "사용 중",
        }

    def __init__(self, *args, church=None, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = User.objects.all().order_by("username")
        if church is not None:
            queryset = queryset.filter(userprofile__church=church).order_by("username")
        self.fields["linked_user"].queryset = queryset
        self.fields["linked_user"].required = False
