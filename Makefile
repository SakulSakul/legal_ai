# legal_ai Step3 — eval 원커맨드 하네스
# Windows 콘솔 cp949 인코딩 이슈 회피를 위해 PYTHONUTF8 강제.

PY ?= python
export PYTHONUTF8 = 1
export PYTHONIOENCODING = utf-8

.PHONY: baseline eval test emb-clear help

help:
	@echo "make baseline   # 현재 코드 baseline 측정 (baseline_locked.json은 보호 — 없을 때만 생성)"
	@echo "make eval       # run_compare.py — baseline 대비 델타 + PASS/FAIL 게이트"
	@echo "make test       # pytest eval/ — 회귀 가드 + 완료기준"
	@echo "make emb-clear  # 임베딩 캐시 삭제"

baseline:
	$(PY) eval/run_baseline.py
	@if [ -f eval/baseline_locked.json ] && [ "$(FORCE)" != "1" ]; then \
		echo "⚠️  eval/baseline_locked.json 이미 존재 — control 보호를 위해 덮어쓰지 않음."; \
		echo "    재고정이 정말 필요하면: make baseline FORCE=1"; \
	else \
		cp $$(ls -t eval/results_*.json | head -1) eval/baseline_locked.json; \
		echo "✅ eval/baseline_locked.json 고정"; \
	fi

eval:
	$(PY) eval/run_compare.py

test:
	$(PY) -m pytest eval/ -v

emb-clear:
	rm -rf eval/.emb_cache
	@echo "🧹 임베딩 캐시 삭제 완료"
