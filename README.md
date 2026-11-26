# Codex 세션 모니터

Codex가 요청한 모델, 서버가 보고한 실제 응답 모델, 재라우팅, 첫 토큰까지 걸린 시간과 출력 토큰 속도를 장비별로 확인합니다. 로컬에서 쓰거나 내 ChatGPT 계정의 비공개 Sites에 여러 장비를 연결할 수 있습니다.

[설치 패키지 다운로드](https://github.com/jinyounghub/codex-session-monitor-kit/releases/latest)

## 장비에 설치

압축을 풀거나 저장소를 복제한 뒤 폴더를 계속 사용할 위치에 둡니다. 릴리스의 `SHA256SUMS`에 설치 패키지의 체크섬이 있습니다. Python 3.11 이상과 GUI가 사용하는 Codex CLI가 필요합니다. Sites를 처음 만들 때는 별도로 Node.js와 Sites가 활성화된 Codex가 필요합니다.

### Windows GUI

GUI를 실행한 상태에서 PowerShell의 `Get-Process codex | Select-Object Path`로 실제 CLI 경로를 확인합니다. GUI를 종료한 뒤 다음 명령을 실행하고 다시 엽니다.

```powershell
.\platforms\windows\setup.ps1 -CodexPath 'C:\path\to\codex.exe'
py -3 -B manage.py site-status
```

설치 프로그램은 Windows의 .NET Framework C# 컴파일러로 CLI 래퍼를 만들고 사용자 로그온 작업을 등록합니다. `py -3`을 쓸 수 없는 환경에서는 설치에 사용한 Python을 지정합니다. Windows GUI가 WSL 백엔드를 사용하면 그 배포판의 Codex 홈과 CLI에 Ubuntu 절차를 적용합니다.

### Ubuntu GUI

실제 CLI와 GUI 실행 파일의 경로를 지정합니다. 사용자 로그인 동안 `codex-session-monitor.service`가 수집기를 실행합니다.

```sh
python3 -B manage.py configure
python3 -B manage.py enable-autostart
CODEX_MONITOR_REAL_CODEX=/path/to/codex ./platforms/ubuntu/start-gui.sh /path/to/codex-gui
python3 -B manage.py site-status
```

이후에도 같은 명령으로 GUI를 열거나, GUI의 CLI 경로 설정을 저장소의 `codex-capture`로 지정합니다. 그 설정에도 실제 CLI 경로를 `CODEX_MONITOR_REAL_CODEX`로 전달합니다. 별도로 설치한 Python을 쓰면 각 명령과 `CODEX_MONITOR_PYTHON`에 같은 실행 파일을 지정합니다.

### macOS

GUI를 종료한 상태에서 수집기를 설정하고 앱을 다시 엽니다.

```sh
python3 -B manage.py configure
python3 -B manage.py enable-autostart
python3 -B manage.py launch-codex
```

기본 앱 위치와 다른 곳에 설치했으면 `CODEX_MONITOR_MAC_APP`에 앱 번들 경로를 지정합니다. 자동으로 찾은 CLI와 실제 GUI가 쓰는 CLI가 다르면 `CODEX_MONITOR_REAL_CODEX`에 실제 바이너리를 지정합니다.

## 내 ChatGPT 계정의 Sites에 연결

Sites 없이도 이 장비의 화면을 열 수 있습니다. [로컬 대시보드](http://127.0.0.1:8765/)

여러 장비를 한 화면에서 보려면 **내 계정으로 소유한 Sites 한 개**를 만들고 그 사이트에 각 장비를 연결합니다. 아래 프롬프트를 Sites가 활성화된 Codex에서 이 패키지 폴더를 열고 붙여넣습니다.

```text
이 설치 패키지로 내 ChatGPT 계정의 소유자 전용 Codex 세션 모니터 Site를 만들고 이 장비를 연결해 줘.

Sites 도구와 스킬을 사용해. 먼저 node scripts/prepare-site.mjs <이 폴더 밖의 새 디렉터리>로 별도 Site 소스를 준비해. 내 계정의 새 비공개 Site를 만들고 반환된 프로젝트 ID를 그 별도 소스의 .openai/hosting.json에만 저장해. 데이터베이스 바인딩 DB를 유지해. 내가 이전에 이 모니터를 배포한 Site를 지정했다면 소유자 전용 권한을 확인하고 그 Site와 전용 소스를 재사용해.

get_site로 이 Site의 서비스 접속 키 siwc_bypass_bearer_token을 받아. site_metrics.py의 auth_headers(token)에 따라 X-Codex-Monitor-Key를 계산하고 이 값을 Sites 런타임 비밀 COLLECTOR_WRITE_KEY로 설정해. 계산은 메모리에서 수행해. 값은 SHA-256의 16진수 표현이고, 입력은 UTF-8 codex-session-monitor/v1 뒤에 NUL 한 바이트와 서비스 키를 붙인 값이야.

별도 소스에서 node scripts/build-site.mjs로 빌드한 뒤 Sites 스킬의 소스 푸시·패키징 절차를 실행하고, 소유자 전용으로 배포해. 서비스 키와 쓰기 키는 도구 메모리와 가려진 표준 입력에서만 다뤄. 개인 Site 설정과 권한 변경은 별도 Site 소스와 Sites 도구에서 처리해.

이 장비에 아직 수집기가 없으면 README의 해당 운영체제 설치 절차를 진행해. 배포가 끝나면 get_site에서 같은 Site의 실제 배포 URL과 서비스 키를 가져와 manage.py connect-site --url <배포 URL> --name <이 장비 이름> --token-stdin으로 연결해. 수집기 실행, 새 수신 시각, 공통 화면의 장비 이름을 확인해. GUI에서 짧은 요청을 실행하고 새 턴의 실제 응답 모델이 서버 응답 출처로 뜨는 것까지 확인해 줘.
```

추가 장비에서는 같은 Sites의 배포 URL과 서비스 키를 사용합니다. `manage.py connect-site --url <내 Site 주소> --name <장비 이름>`은 가려진 입력으로 키를 받습니다. Codex 도구로 연결할 때는 `--token-stdin`으로 직접 전달합니다. 보는 사람은 해당 Site를 소유한 ChatGPT 계정으로 로그인합니다.

## 측정값과 수집 범위

- **선택 모델**은 Codex의 요청 모델과 추론 강도입니다. **실제 응답 모델**은 서버가 보고한 모델 ID입니다. 요청한 모델과 다르면 해당 턴에 재라우팅으로 표시되며, 연결된 ID가 없으면 미보고로 표시됩니다.
- **첫 토큰**은 Codex의 턴별 TTFT입니다. **토큰/초**는 서버가 보고한 출력 토큰을 각 모델 스트림의 관측된 생성 시간으로 나눈 추정치입니다.
- 수집기는 응답에서 모델 ID와 응답 ID를 메모리에서 추출해 같은 장비의 사용량 기록에 연결합니다. Site에 보낼 때는 장비 이름, 채팅 제목, 가명 처리한 세션·턴 ID, 모델과 속도 측정값만 담습니다. Site는 장비별 최근 스냅샷을 보관합니다.

`manage.py site-status`는 장비 수집기 실행 여부와 마지막 Site 수신 시각을 보여줍니다. `manage.py local-dashboard off`로 로컬 웹 화면을 닫고 수집을 계속할 수 있습니다. `manage.py disconnect-site`로 장비의 Site 전송 연결을 해제합니다.

이 배포판은 자신의 Codex 세션을 모니터링하는 용도로 내려받아 설치하고 사용할 수 있습니다.
