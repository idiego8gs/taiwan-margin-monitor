"""Local research CLI: freeze, run, resume and export."""
import argparse,json
from datetime import date

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    f=commands.add_parser('freeze');f.add_argument('--start',default='2021-10-01');f.add_argument('--end')
    r=commands.add_parser('run');r.add_argument('--snapshot',required=True);r.add_argument('--workers',type=int,default=4);r.add_argument('--draws',type=int,default=200000)
    r=commands.add_parser('resume');r.add_argument('--run-id',required=True);r.add_argument('--workers',type=int,default=4)
    r=commands.add_parser('export');r.add_argument('--run-id',required=True)
    args=parser.parse_args()
    if args.command=='freeze':
        from .research_freeze import freeze
        result=freeze(date.fromisoformat(args.start),date.fromisoformat(args.end) if args.end else None)
    else:
        from .research_runner import run,resume,export
        if args.command=='run':result=run(args.snapshot,workers=args.workers,draws=args.draws)
        elif args.command=='resume':result=resume(args.run_id,workers=args.workers)
        else:result=export(args.run_id)
    print(json.dumps(result,ensure_ascii=False,default=str),flush=True)

if __name__=='__main__':main()
