# Audio Loopback Stability Checklist

このメモは Google Meet のループバック音声を常に PipeWire monitor から取得できるようにするための手順です。

## 1. ループバック入力の確認

```bash
source .venv311/bin/activate
python -m transcriber.cli --list-devices
```

`pipewire` (または `default`) の index を `.env` の `AUDIO_DEVICE_INDEX` に設定します。  
本リポジトリでは `AUDIO_DEVICE_INDEX=6` に更新済みです。

## 2. 既定サウンドデバイスの固定

`scripts/wp-force-monitor.sh` を `~/bin` に配置し、実行権限を付けます。

```bash
install -Dm755 scripts/wp-force-monitor.sh ~/bin/wp-force-monitor.sh
~/bin/wp-force-monitor.sh
```

このスクリプトは以下を強制します（SINK_NAME を未設定のままにするとシンクには触れません）。

- 既定ソース: `alsa_output.pci-0000_00_1f.3.analog-stereo.monitor`
- （任意）`SINK_NAME` を指定した場合のみ既定シンクも変更

## 3. WirePlumber 状態監視の systemd 化

`systemd/wp-force-monitor.service` と `.path` をユーザー単位の systemd ディレクトリへコピーします。  
SINK_NAME を固定したい場合は `Environment=SINK_NAME=...` を `~/.config/systemd/user/wp-force-monitor.service.d/override.conf` などで設定します。

```bash
mkdir -p ~/.config/systemd/user
cp systemd/wp-force-monitor.{service,path} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now wp-force-monitor.service wp-force-monitor.path
```

`.path` ユニットが `~/.local/state/wireplumber/default-nodes` を監視し、  
ポート切り替えや GNOME 操作で monitor 以外が記録された場合に自動で元へ戻します。

## 4. 動作確認

```bash
pactl info | grep -E 'デフォルト(シンク|ソース)'
wpctl status | sed -n 's/.*Audio\/Source\s\+//p'
```

`デフォルトソース` が `alsa_output...monitor` になっていれば OK です。  
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
