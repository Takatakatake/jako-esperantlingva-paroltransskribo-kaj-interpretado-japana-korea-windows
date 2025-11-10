# Windows で静音ループバック文字起こしが安定した理由まとめ

このメモは、`windows 上手く行った設定/` に保存した各種スクリーンショットと `.env` を参照しながら、PC 出力（YouTube / Discord など）を VB-Audio Virtual Cable で回収しつつ、イヤホンだけに音を流し、高精度な Speechmatics 文字起こしを実現できた構成を整理したものです。

## 0. PowerShell と仮想環境の準備

1. PowerShell の実行ポリシーを `RemoteSigned` に統一し、`Activate.ps1` をブロックされないようにした。
   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
   Set-ExecutionPolicy -Scope LocalMachine -ExecutionPolicy RemoteSigned
   ```
2. `.venv311\Scripts\Activate.ps1` を実行して仮想環境を有効化。
3. `python -m transcriber.cli --list-devices` で `CABLE Output (VB-Audio Virtual Cable)` の **WASAPI** インデックス (#24) を確認。以降 `.env` の `AUDIO_DEVICE_INDEX=24` が変わらない前提で運用する。

## 1. ループバック経路とデバイスの役割

| 役割 | 設定内容 | スクリーンショット |
|------|----------|---------------------|
| 再生アプリ (Chrome/Discord) | 出力デバイスを `CABLE Input`, 入力デバイスを `CABLE Output` に設定（音量ミキサーでアプリ単位に Fix）。 | `volume-mixer-chrome-loopback.png`, `volume-mixer-discord-loopback.png` |
| VB-Cable | `CABLE Input` が仮想再生デバイス、`CABLE Output` が仮想録音デバイス。 | `cable-input-properties-16khz.png`, `cable-output-properties-16khz.png` |
| Transcriber | `.env` で `AUDIO_DEVICE_INDEX=24` を指定し、常に WASAPI の `CABLE Output` から音を取得。 | `.env` 設定 |
| イヤホン (Realtek) | システム既定の再生デバイスをヘッドホンに維持。`recording-tab-cable-output-listen.png` の通り「このデバイスを聴く」をオンにしてイヤホンへモニター。 | `sound-output-default-headphones.png`, `recording-tab-cable-output-listen.png` |

この構成により、他アプリや通知音はそのままイヤホンへ流しつつ、必要なアプリだけが VB-Cable を経由して文字起こしパイプラインに届く。

## 2. サンプルレートとチャンネルの完全統一

| 設定箇所 | 値 | 目的 |
|----------|----|------|
| `.env` (`AUDIO_DEVICE_SAMPLE_RATE`, `AUDIO_SAMPLE_RATE`) | 16000 Hz | 物理デバイスとアプリ内部の処理レートを一致させ、ドライバ側の再サンプルをなくす。 |
| `.env` (`AUDIO_CHANNELS`) | 1 (Mono) | Speechmatics が期待する入力形式に合わせる。 |
| Windows 詳細設定 (`CABLE Input` / `CABLE Output`) | 16-bit / 16 kHz | `.env` と同じ値にし、レベル揺らぎや遅延を消す。 |

以前は 48 kHz → 16 kHz 変換が別々に走っていたため音量が不安定だったが、全箇所を 16 kHz に揃えることで波形の振幅が安定し、精度が一気に改善した。

## 3. 音量とモニタリング

- システム音量：出力 71%、入力 75%。
- アプリ音量：Chrome／Discord／`CABLE Output` をいずれも 100% に保つ。
- 録音タブで `CABLE Output` のレベルメーターが振れているかを常に確認。
- 「このデバイスを聴く」をオンにしてイヤホンへ返すことで、耳と目（メーター）の両方で信号を監視できる。

## 4. `.env` の抜粋

```ini
# Google Meet loopback audio device (from `--list-devices`)
AUDIO_DEVICE_INDEX=24
AUDIO_DEVICE_SAMPLE_RATE=16000
AUDIO_SAMPLE_RATE=16000
AUDIO_CHANNELS=1
AUDIO_CHUNK_DURATION_SECONDS=0.5
AUDIO_WINDOWS_LOOPBACK_DEVICE=CABLE Output (VB-Audio Virtual Cable)
```

`python -m transcriber.cli --diagnose-audio` を実行すると、設定済みデバイスが「#24 CABLE Output …」と表示され、ループバック経路が正しく捕捉されていることを確かめられる。

## 5. マイク（音声入力）を死なせないコツ

1. Windows の既定入力デバイスは実マイク（例: `マイク (2.0 Camera)`）に戻す。`sound-input-default-camera-mic.png` 参照。
2. Chrome / Discord の入力デバイスを音量ミキサーで **必要に応じて切り替える**。ChatGPT の音声入力を使う場合は Chrome の入力を実マイクにし、出力だけを `CABLE Input` にすれば、ループバックを保ったまま音声入力が復活する。
3. `chrome://settings/content/microphone` で使用マイクを明示し、サイトごとに許可状態を確認する。

これで「PC 出力の文字起こし」と「マイクによる音声入力」を同時に成立させられる。

## 6. 成功の要因まとめ

1. **デバイスの役割分離** … 物理既定は Realtek のまま、Chrome/Discord だけを VB-Cable に流す。
2. **サンプルレート完全一致** … ハードとソフトをすべて 16 kHz / Mono に統一。
3. **双方向モニタリング** … 録音タブ＋「このデバイスを聴く」で視覚・聴覚の両方から確認。
4. **音量管理** … システム／アプリ／VB-Cable の各段を 100% 付近で維持し、低レベル入力を防止。
5. **診断ツール活用** … `--list-devices` と `--diagnose-audio`、`logs/meet-session.log` で常に経路を可視化。

以上を揃えた結果、PC 出力を漏らさず回収しつつ、イヤホンのみから音を出し、Speechmatics で実用的な文単位の文字起こしが行えるようになった。
