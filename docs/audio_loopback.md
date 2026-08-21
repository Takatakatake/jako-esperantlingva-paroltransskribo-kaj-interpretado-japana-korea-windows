# Audio Loopback Stability Checklist

このメモは Google Meet のループバック音声を常に PipeWire monitor から取得できるようにするための手順です。

初回セットアップの全体像、`codex_transcribe` 仮想 sink、Speechmatics の `standard` / `enhanced` 切り替えまで含めた手順は、リポジトリ直下の `Ubuntu音声環境のセットアップ方法.md` を参照してください。

## 1. ループバック入力の確認

```bash
source .venv311/bin/activate
python -m transcriber.cli --check-environment
python -m transcriber.cli --list-devices
python -m transcriber.cli --diagnose-audio
# ガイド付きの手順を確認したい場合は `python -m transcriber.cli --setup-wizard` も参照してください。
# 既定デバイスが仮想のまま残った場合は `bash scripts/reset_audio_defaults.sh` で元に戻せます。
```

`pipewire` (または `default`) の index を `.env` の `AUDIO_DEVICE_INDEX` に設定します。  
本リポジトリでは `AUDIO_DEVICE_INDEX=6` に更新済みです。

ハードが 48 kHz 固定の場合でも、`.env` の `AUDIO_DEVICE_SAMPLE_RATE=48000` と `AUDIO_SAMPLE_RATE=16000` を併用すれば自動的に 16 kHz へ変換されます。サンプル長は `AUDIO_CHUNK_DURATION_SECONDS`（推奨 0.1〜0.5 秒）で調整してください。

ループバック自動設定を有効にしたまま `python -m transcriber.cli --diagnose-audio` を実行すると、  
モニターデバイス候補・設定上の注意点が一覧で確認できます。

## 2. 既定サウンドデバイスの固定

`scripts/wp-force-monitor.sh` を `~/bin` に配置し、実行権限を付けます。

通常運用（`python -m transcriber.cli` の自動構成）ではどちらのスクリプトも不要です。手動でルーティングを組む場合のみ、用途に応じて**どちらか一方**を使います。

```bash
# A) 仮想 sink + monitor を明示的に構成する（VB-CABLE 相当の経路を作る）
install -Dm755 scripts/setup_audio_loopback_linux.sh ~/bin/setup-audio-loopback.sh
HEADPHONE_SINK=<物理シンク名> ~/bin/setup-audio-loopback.sh   # 名前は `pactl list short sinks` で確認

# B) 既存 monitor を既定ソースに固定するだけ
install -Dm755 scripts/wp-force-monitor.sh ~/bin/wp-force-monitor.sh
~/bin/wp-force-monitor.sh
```

このスクリプトは以下を強制します（SINK_NAME を未設定のままにするとシンクには触れません）。

- 既定ソース: `SOURCE_NAME` で指定した monitor（未指定時は `codex_transcribe.monitor`、無ければ現在の既定シンクの monitor を自動選択）
- （任意）`SINK_NAME` を指定した場合のみ既定シンクも変更
- このスクリプト自体は設定を書き換えるだけで、自動では元に戻しません。元に戻すのは `python -m transcriber.cli` の終了時復元、または `scripts/reset_audio_defaults.sh` です。

> ⚠️ **併用注意**: `python -m transcriber.cli` の自動ループバック構成を使う場合、後述の systemd ユニットは有効化しないでください。ユニットが既定ソースを強制的に書き戻し、CLI 側の設定・終了時復元と競合します。ユニットは「CLI を使わず手動ルーティングで運用する」場合の選択肢です。

## 3. WirePlumber 状態監視の systemd 化

`systemd/wp-force-monitor.service` と `.path` をユーザー単位の systemd ディレクトリへコピーします。  
SINK_NAME を固定したい場合は `Environment=SINK_NAME=...` を `~/.config/systemd/user/wp-force-monitor.service.d/override.conf` などで設定します。

```bash
mkdir -p ~/.config/systemd/user
cp systemd/wp-force-monitor.{service,path} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now wp-force-monitor.path  # 監視は .path のみ有効化
systemctl --user start wp-force-monitor.service      # 必要なら今すぐ 1 回実行
```

（`.service` 側は起動時自動実行しない構成です。ログイン直後は PipeWire のデバイス列挙が終わっておらず、誤った monitor を固定しかねないためです。）

`.path` ユニットが `~/.local/state/wireplumber/default-nodes` を監視し、  
ポート切り替えや GNOME 操作で monitor 以外が記録された場合に自動で元へ戻します。

## 4. 動作確認

```bash
pactl info | grep -E 'デフォルト(シンク|ソース)'
wpctl status | sed -n 's/.*Audio\/Source\s\+//p'
```

`デフォルトソース` が monitor（A の構成なら `codex_transcribe.monitor`、B なら `alsa_output...monitor` など）になっていれば OK です。  
Transcriber を起動したら `logs/meet-session.log` に長めの文が戻っているか確認してください。

## 5. ワンコマンド起動 (Web UI + 翻訳込み)

Web UI がポート 8765 を掴んだまま残ると次回 8766 以降にずれてしまうため、以下のラッパースクリプトを用意しています。

```bash
install -Dm755 scripts/run_transcriber.sh ~/bin/run-transcriber.sh
```

以後は

```bash
~/bin/run-transcriber.sh
```

だけで

- 8765 で LISTEN している古い Web UI を自動停止（ブラウザの接続は維持）
- `python -m transcriber.cli --backend=speechmatics --log-level=INFO`

が起動し、ブラウザも自動で 8765 を開きます。翻訳 (Google, ja/ko) も同じ WebSocket で配信されるため、UI 上で即座に確認できます。
