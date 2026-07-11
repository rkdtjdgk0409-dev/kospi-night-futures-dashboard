# 코스피 야간선물 노션 임베드

Hang on! 코스피 야간선물 화면에서 현재 가격·등락률·차트가 들어 있는 패널만 캡처해 GitHub Pages로 배포합니다.

## 설치

1. GitHub에서 `kospi-night-futures-dashboard`라는 Public 저장소 생성
2. 이 압축파일 내부 파일 전체 업로드
3. Settings → Pages → Source를 `GitHub Actions`로 선택
4. Actions → 코스피 야간선물 자동 갱신 → Run workflow
5. 생성된 Pages 주소를 노션 `/embed`로 삽입

## 갱신 주기

매일 30분마다 실행합니다.

## 주의

원본 사이트의 화면 구조가 바뀌면 `update.py` 수정이 필요할 수 있습니다.
