# 코스피 야간선물 GitHub Pages · Notion 차트

Hang on!의 코스피 야간선물 카드에서 현재 선물 지수, 등락폭, 등락률과 원본 차트의 전체 시계열을 가져와 GitHub Pages에 배포합니다.

## 적용 방법

1. GitHub의 `kospi-night-futures-dashboard` 저장소에서 기존 파일을 모두 삭제하지 말고, 이 폴더 안의 파일과 폴더를 같은 위치에 덮어씁니다.
2. 특히 `.github/workflows/update.yml`이 반드시 그대로 올라가야 합니다. GitHub 웹 업로드에서 숨김 폴더가 빠지지 않았는지 확인하세요.
3. 저장소의 **Settings → Pages → Build and deployment → Source**를 **GitHub Actions**로 선택합니다.
4. **Actions → 코스피 야간선물 자동 갱신 → Run workflow**를 한 번 실행합니다.
5. 완료 후 아래 주소가 차트 페이지입니다.

   `https://rkdtjdgk0409-dev.github.io/kospi-night-futures-dashboard/`

6. Notion에서 `/embed`를 입력하고 위 주소를 붙여 넣습니다. 권장 임베드 높이는 약 540px입니다.

## 자동 갱신

- 한국시간 18:02부터 다음 날 05:57까지 5분마다 실행합니다.
- GitHub Actions 예약 실행은 GitHub 사정에 따라 몇 분 늦어질 수 있습니다.
- 원본 차트의 배열을 매번 새로 가져오며 이전 실행분을 합치거나 임의로 최근 N일만 자르지 않습니다.
- 원본 전체 시계열을 가져오지 못한 실행은 실패 처리하므로, 불완전한 차트가 새로 배포되지 않습니다.

## 이번 수정에서 해결한 문제

- 원본 페이지가 하락폭을 `47.2`처럼 부호 없이 표시해도 등락률의 부호로 정확히 하락을 판단합니다.
- 존재하지 않는 `panel.png`를 복사하던 자동 배포 오류를 제거했습니다.
- 저장소 루트의 `update.yml`이 아니라 실제로 작동하는 `.github/workflows/update.yml`을 사용합니다.
- 과거 데이터를 4일간 계속 합치고 2,500개로 자르던 로직을 제거해 원본 페이지와 같은 시작·종료 시각을 표시합니다.

