import argparse
from materflow.pipeline import process_run, STAGES


def main():
    parser = argparse.ArgumentParser(description="MaterFlow: visual observations → events → material lineage")
    parser.add_argument("--config", required=True, help="run YAML; paths within YAML are repository-relative")
    parser.add_argument("--stage", choices=(*STAGES, "all"), default="all")
    args = parser.parse_args()
    process_run(args.config, args.stage)


if __name__ == "__main__":
    main()
