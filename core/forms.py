from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

from .models import MemberRole


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
