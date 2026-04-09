from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import (
    MemberRole,
    Sermon,
    SermonHighlightChoice,
    SermonHighlightVote,
    SermonStatus,
    SermonSummary,
    UserProfile,
    WeeklyChallenge,
)
from core.services.engagement import complete_mission, submit_daily_quiz, submit_reflection
from reports.services import (
    sync_content_quality_report,
    sync_daily_action_report,
    sync_sermon_participation_report,
    sync_user_participation_report,
    sync_weekly_participation_report,
)


User = get_user_model()


class Command(BaseCommand):
    help = "교역자 리포트 화면을 확인할 수 있는 데모 데이터를 생성합니다."

    demo_username_prefix = "demo_member"
    demo_sermon_title = "[DEMO] 리포트 미리보기 설교"

    def add_arguments(self, parser):
        parser.add_argument(
            "--keep-existing",
            action="store_true",
            help="기존 데모 데이터를 지우지 않고 유지합니다.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not options["keep_existing"]:
            self._clear_demo_data()

        today = timezone.localdate()
        week_start = today - timedelta(days=min(today.weekday(), 1))
        week_end = week_start + timedelta(days=4)

        sermon = Sermon.objects.create(
            title=self.demo_sermon_title,
            preacher="데모 목사",
            sermon_date=week_start,
            bible_passage="사도행전 4:1-12",
            transcript=(
                "성령께서 막으시는 자리에서도 하나님의 뜻은 멈추지 않습니다. "
                "우리가 계획한 길이 막힐 때에도 하나님은 더 좋은 길을 준비하십니다. "
                "복음은 사람의 계산보다 크고, 순종은 이해보다 먼저입니다. "
                "교회는 익숙한 방식만 붙드는 공동체가 아니라 성령의 인도하심을 듣는 공동체입니다."
            ),
            ai_generated=True,
            status=SermonStatus.PUBLISHED,
            is_published=True,
            published_at=timezone.now(),
        )

        SermonSummary.objects.create(
            sermon=sermon,
            overview="막히는 순간에도 성령의 인도하심을 따라 순종할 때 복음의 길은 다시 열립니다.",
            outline_points=[
                "복음은 우리의 계획보다 크다는 사실을 기억합니다.",
                "막히는 경험도 하나님의 인도하심일 수 있습니다.",
                "성령의 인도하심을 듣기 위해 멈추어야 할 때가 있습니다.",
                "교회는 익숙함보다 순종을 선택해야 합니다.",
                "하나님은 막힌 길 너머에 다른 길을 준비하십니다.",
                "복음의 문은 사람의 계산으로 닫히지 않습니다.",
                "순종은 이해보다 먼저일 때가 많습니다.",
                "성령의 흐름을 따를 때 공동체가 새로워집니다.",
            ],
            summary_line1="하나님은 막히는 순간에도 뜻을 이루십니다.",
            summary_line2="익숙한 계획보다 성령의 인도하심이 우선입니다.",
            summary_line3="순종하는 교회가 복음의 길을 다시 엽니다.",
            key_point1="막힌 자리에서도 하나님의 계획은 계속 움직입니다.",
            key_point2="성령의 인도는 우리의 기대와 다른 길로 이끄실 수 있습니다.",
            key_point3="교회는 이해보다 순종을 먼저 배워야 합니다.",
            ai_generated=True,
            approved=True,
        )

        highlight_choices = [
            SermonHighlightChoice.objects.create(
                sermon=sermon,
                order=index,
                text=text,
                ai_generated=True,
            )
            for index, text in enumerate(
                [
                    "막힌 자리에서도 하나님의 계획은 계속 움직입니다.",
                    "익숙한 길이 막힐 때 성령의 새 길을 기다리십시오.",
                    "순종은 이해가 끝난 뒤가 아니라 부르심 앞에서 시작됩니다.",
                ],
                start=1,
            )
        ]

        challenge = WeeklyChallenge.objects.create(
            sermon=sermon,
            title="[DEMO] 5일 루틴",
            week_start=week_start,
            week_end=week_end,
            is_active=True,
        )

        daily_specs = [
            {
                "title": "막힘 속에서도 순종하기",
                "intro": "막힌 상황 속에서도 하나님의 인도하심을 먼저 묻는 하루입니다.",
                "quiz_question": "설교의 핵심은 무엇이었나요?",
                "choices": ["성령의 인도", "재정 관리", "예배 시간", "봉사 순서"],
                "answer": "성령의 인도",
                "explanation": "설교는 막힘 속에서도 성령의 인도를 따르는 순종을 강조했습니다.",
                "reflection": "최근 막혀 보였지만 하나님이 인도하신다고 느낀 순간은 언제였나요?",
                "mission_title": "오늘 순종 메모",
                "mission_description": "오늘 순종해야 할 한 가지를 적고 실천해 보세요.",
            },
            {
                "title": "익숙함보다 인도하심",
                "intro": "익숙한 방식보다 하나님의 뜻을 더 귀하게 여기는 날입니다.",
                "quiz_question": "교회가 먼저 배워야 할 것은 무엇인가요?",
                "choices": ["순종", "규칙", "속도", "행사"],
                "answer": "순종",
                "explanation": "설교는 교회가 이해보다 순종을 먼저 배워야 한다고 말했습니다.",
                "reflection": "나는 무엇을 붙들고 있어서 새로운 인도하심을 놓치고 있나요?",
                "mission_title": "낯선 순종 한 가지",
                "mission_description": "평소 하지 않던 작은 순종 한 가지를 실천해 보세요.",
            },
            {
                "title": "새 길을 기다리기",
                "intro": "하나님이 준비하신 새 길을 기대하며 기다리는 날입니다.",
                "quiz_question": "막힌 길 너머에 하나님이 준비하신 것은?",
                "choices": ["새 길", "포기", "두려움", "지연"],
                "answer": "새 길",
                "explanation": "설교는 하나님이 막힌 길 너머에 다른 길을 준비하신다고 전했습니다.",
                "reflection": "내가 서둘러 결론 내린 일 중 기다려야 할 일은 무엇인가요?",
                "mission_title": "기다림의 기도",
                "mission_description": "조급한 마음을 내려놓고 5분간 기도해 보세요.",
            },
            {
                "title": "복음의 문은 닫히지 않는다",
                "intro": "사람의 계산보다 큰 복음의 길을 믿는 날입니다.",
                "quiz_question": "복음의 문은 무엇으로 닫히지 않나요?",
                "choices": ["사람의 계산", "하나님의 은혜", "기도", "말씀"],
                "answer": "사람의 계산",
                "explanation": "복음의 문은 사람의 계산으로 닫히지 않는다고 설교했습니다.",
                "reflection": "나는 어디에서 너무 사람의 계산만 하고 있나요?",
                "mission_title": "복음 한마디",
                "mission_description": "가족이나 가까운 사람에게 복음의 소망을 한마디 전해 보세요.",
            },
            {
                "title": "공동체의 새로움",
                "intro": "성령의 흐름을 따를 때 공동체가 새로워짐을 기억하는 날입니다.",
                "quiz_question": "공동체를 새롭게 하는 것은 무엇인가요?",
                "choices": ["성령의 흐름", "행사 준비", "건물 확장", "규정 강화"],
                "answer": "성령의 흐름",
                "explanation": "성령의 흐름을 따를 때 공동체가 새로워진다고 설교했습니다.",
                "reflection": "우리 공동체가 새로워지기 위해 필요한 순종은 무엇일까요?",
                "mission_title": "공동체를 위한 기도",
                "mission_description": "교회와 목회자를 위해 짧게라도 기도해 보세요.",
            },
        ]

        daily_items = []
        for day_number, spec in enumerate(daily_specs, start=1):
            daily_items.append(
                challenge.daily_engagements.create(
                    sermon=sermon,
                    day_number=day_number,
                    title=spec["title"],
                    intro=spec["intro"],
                    quiz_question=spec["quiz_question"],
                    quiz_choice1=spec["choices"][0],
                    quiz_choice2=spec["choices"][1],
                    quiz_choice3=spec["choices"][2],
                    quiz_choice4=spec["choices"][3],
                    quiz_answer=spec["answer"],
                    quiz_explanation=spec["explanation"],
                    reflection_question=spec["reflection"],
                    mission_title=spec["mission_title"],
                    mission_description=spec["mission_description"],
                    ai_generated=True,
                    approved=True,
                )
            )

        demo_users = self._create_demo_users()
        self._create_demo_votes(sermon, highlight_choices, demo_users)
        self._create_demo_engagements(daily_items, demo_users)

        weekly_report = sync_weekly_participation_report(challenge)
        sermon_report = sync_sermon_participation_report(sermon)
        daily_report = sync_daily_action_report(challenge)
        quality_report = sync_content_quality_report(challenge)
        for user in demo_users:
            sync_user_participation_report(user)

        self.stdout.write(self.style.SUCCESS("데모 리포트 데이터를 생성했습니다."))
        self.stdout.write(f"- 설교: {sermon.title}")
        self.stdout.write(f"- 주간 참여자: {weekly_report.participant_count}명")
        self.stdout.write(f"- 설교 참여자: {sermon_report.participant_count}명")
        self.stdout.write(f"- Day 리포트 행 수: {len(daily_report.day_rows)}")
        self.stdout.write(f"- 품질 이슈 수: {quality_report.issue_count}")
        self.stdout.write("- 확인 주소: http://localhost:8000/pastor/reports/")

    def _clear_demo_data(self):
        User.objects.filter(username__startswith=self.demo_username_prefix).delete()
        Sermon.objects.filter(title=self.demo_sermon_title).delete()

    def _create_demo_users(self):
        users = []
        specs = [
            ("demo_member1", "김은혜", MemberRole.MEMBER),
            ("demo_member2", "박순종", MemberRole.DEACON),
            ("demo_member3", "이감사", MemberRole.KWONSA),
            ("demo_member4", "최믿음", MemberRole.ELDER),
        ]
        for username, first_name, role in specs:
            user = User.objects.create_user(
                username=username,
                password="demo1234!",
                first_name=first_name,
            )
            UserProfile.objects.update_or_create(
                user=user,
                defaults={"member_role": role, "points": 0, "streak_days": 0},
            )
            users.append(user)
        return users

    def _create_demo_votes(self, sermon, choices, users):
        vote_map = [0, 0, 1, 2]
        for user, choice_index in zip(users, vote_map):
            SermonHighlightVote.objects.create(
                user=user,
                sermon=sermon,
                choice=choices[choice_index],
            )

    def _create_demo_engagements(self, daily_items, users):
        # 1번, 2번 사용자는 거의 완주, 3번은 중간 참여, 4번은 초반만 참여하도록 구성
        patterns = {
            users[0].username: [True, True, True, True, True],
            users[1].username: [True, True, True, True, False],
            users[2].username: [True, True, False, False, False],
            users[3].username: [True, False, False, False, False],
        }

        for user in users:
            joined_days = patterns[user.username]
            for index, joined in enumerate(joined_days):
                if not joined:
                    continue
                daily = daily_items[index]
                quiz_answer = daily.quiz_answer if user.username != users[2].username else daily.quiz_choice2
                submit_daily_quiz(user=user, daily_engagement=daily, selected_answer=quiz_answer)
                submit_reflection(
                    user=user,
                    daily_engagement=daily,
                    response_text=f"{user.first_name}의 데모 묵상 응답입니다. 오늘 말씀을 삶에 적용해 보겠습니다.",
                )
                complete_mission(
                    user=user,
                    daily_engagement=daily,
                    note=f"{user.first_name}이(가) 데모 미션을 완료했습니다.",
                )
