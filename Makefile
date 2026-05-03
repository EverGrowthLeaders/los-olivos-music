.PHONY: install test doctor demo-render lint zip

install:
	python -m pip install -e ".[dev]"

test:
	PYTHONPATH=src pytest -q

doctor:
	PYTHONPATH=src python -m yt_music_factory.cli doctor

demo-render:
	PYTHONPATH=src python -m yt_music_factory.cli render examples/local_demo.yaml --workdir runs --no-upload

lint:
	ruff check src tests

zip:
	cd .. && zip -r yt-music-factory.zip yt-music-factory -x 'yt-music-factory/.git/*' 'yt-music-factory/runs/*'
