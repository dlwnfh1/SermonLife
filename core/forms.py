import re

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

from .models import DailyEngagement, MemberRole, Sermon, SermonSummary


User = get_user_model()


class SermonLifeSignUpForm(UserCreationForm):
    first_name = forms.CharField(label="이름", max_length=150, required=False)
    member_role = forms.ChoiceField(
        label="직분/구분",
        choices=MemberRole.choices,
        required=False,
        initial=MemberRole.MEMBER,
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "member_role", "password1", "password2")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "아이디"
        self.fields["username"].help_text = ""
        self.fields["password1"].label = "비밀번호"
        self.fields["password1"].help_text = ""
        self.fields["password2"].label = "비밀번호 확인"
        self.fields["password2"].help_text = ""

        self.fields["username"].widget.attrs.update({"placeholder": "아이디"})
        self.fields["first_name"].widget.attrs.update({"placeholder": "이름(선택)"})
        self.fields["password1"].widget.attrs.update({"placeholder": "비밀번호"})
        self.fields["password2"].widget.attrs.update({"placeholder": "비밀번호 확인"})

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("이미 사용 중인 아이디입니다.")
        return username


def _format_transcript_for_pastor_edit(value):
    if not value:
        return ""

    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if normalized.count("\n\n") >= 2:
        return normalized

    compact = re.sub(r"\n+", " ", normalized)
    compact = re.sub(r"\s+", " ", compact).strip()
    if not compact:
        return normalized

    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?。！？])\s+", compact)
        if sentence.strip()
    ]
    if len(sentences) <= 2:
        return normalized

    step = 3 if len(sentences) > 12 else 2
    paragraphs = [
        " ".join(sentences[index:index + step]).strip()
        for index in range(0, len(sentences), step)
    ]
    return "\n\n".join(paragraphs)


class PastorSermonEditForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and getattr(self.instance, "pk", None):
            self.initial["transcript"] = _format_transcript_for_pastor_edit(self.instance.transcript)

    class Meta:
        model = Sermon
        fields = ("title", "preacher", "sermon_date", "bible_passage", "transcript")
        widgets = {
            "sermon_date": forms.DateInput(attrs={"type": "date"}),
            "bible_passage": forms.TextInput(attrs={"placeholder": "본문"}),
            "transcript": forms.Textarea(
                attrs={
                    "rows": 26,
                    "style": "min-height: 720px;",
                }
            ),
        }
        labels = {
            "title": "설교 제목",
            "preacher": "설교자",
            "sermon_date": "설교일",
            "bible_passage": "본문",
            "transcript": "자막 및 원문",
        }


class PastorSermonSummaryForm(forms.ModelForm):
    class Meta:
        model = SermonSummary
        fields = (
            "overview",
            "summary_line1",
            "summary_line2",
            "summary_line3",
            "key_point1",
            "key_point2",
            "key_point3",
        )
        widgets = {
            "overview": forms.Textarea(attrs={"rows": 7, "style": "min-height: 160px;"}),
            "key_point1": forms.Textarea(attrs={"rows": 3}),
            "key_point2": forms.Textarea(attrs={"rows": 3}),
            "key_point3": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "overview": "설교 개요",
            "summary_line1": "요약 1",
            "summary_line2": "요약 2",
            "summary_line3": "요약 3",
            "key_point1": "핵심 메시지 1",
            "key_point2": "핵심 메시지 2",
            "key_point3": "핵심 메시지 3",
        }


class PastorDailyEngagementForm(forms.ModelForm):
    class Meta:
        model = DailyEngagement
        fields = (
            "title",
            "intro",
            "quiz_question",
            "quiz_choice1",
            "quiz_choice2",
            "quiz_choice3",
            "quiz_choice4",
            "quiz_answer",
            "quiz_explanation",
            "reflection_question",
            "mission_title",
            "mission_description",
        )
        widgets = {
            "intro": forms.Textarea(attrs={"rows": 4}),
            "quiz_explanation": forms.Textarea(attrs={"rows": 3}),
            "reflection_question": forms.Textarea(attrs={"rows": 3}),
            "mission_description": forms.Textarea(attrs={"rows": 3}),
        }
