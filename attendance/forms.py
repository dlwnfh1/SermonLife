from django import forms
from django.contrib.auth import get_user_model

from .models import AttendanceDistrict, AttendanceDistrictLeader, AttendanceGroup, AttendanceMember


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
        fields = ["guide", "leader"]

    def __init__(self, *args, group=None, district=None, **kwargs):
        super().__init__(*args, **kwargs)
        target_group = group or (self.instance if getattr(self.instance, "pk", None) else None)

        member_queryset = AttendanceMember.objects.none()
        if target_group:
            member_queryset = AttendanceMember.objects.filter(group=target_group, is_active=True).order_by(
                "sort_order",
                "name",
                "id",
            )
        self.fields["guide"].queryset = member_queryset
        self.fields["leader"].queryset = member_queryset


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
