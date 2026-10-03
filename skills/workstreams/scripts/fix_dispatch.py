import re

with open('workstreams.py', 'r') as f:
    content = f.read()

# Fix the command dispatch section
old = '''    print(f"DEBUG: command={args.command}"); if args.command == "init":
        manager.init_project(force=args.force, num_workstreams=args.workstreams)
    elif args.command == "start":
        manager.start(workstream_id=args.workstream, command=args.command)
    elif args.command == "status":
        manager.status(workstream_id=args.workstream, verbose=args.verbose)
    elif args.command == "assign":
        manager.assign(args.workstream, args.issue)
    elif args.command == "sync":
        manager.sync(workstream_id=args.workstream, rebase=args.rebase)
    elif args.command == "pr":
        manager.pr(args.workstream, args.title, args.body, args.base, args.draft)
    elif args.command == "merge":
        manager.merge(args.workstream, args.method, args.delete_branch, args.auto)
    elif args.command == "dispatch":
        manager.dispatch(args.workstream, args.subagent, args.issue, args.prompt)
    elif args.command == "logs":
        manager.logs(args.workstream, args.follow, args.lines)
    elif args.command == "run":
        manager.run(args.workstream, args.command)
    elif args.command == "status":
        manager.status(workstream_id=args.workstream, verbose=args.verbose)
    elif args.command == "cleanup":
        pass  # TODO'''

new = '''    print(f"DEBUG: command={args.command}")
    if args.command == "init":
        manager.init_project(force=args.force, num_workstreams=args.workstreams)
    elif args.command == "start":
        manager.start(workstream_id=args.workstream, command=args.command)
    elif args.command == "status":
        manager.status(workstream_id=args.workstream, verbose=args.verbose)
    elif args.command == "assign":
        manager.assign(args.workstream, args.issue)
    elif args.command == "sync":
        manager.sync(workstream_id=args.workstream, rebase=args.rebase)
    elif args.command == "pr":
        manager.pr(args.workstream, args.title, args.body, args.base, args.draft)
    elif args.command == "merge":
        manager.merge(args.workstream, args.method, args.delete_branch, args.auto)
    elif args.command == "dispatch":
        manager.dispatch(args.workstream, args.subagent, args.issue, args.prompt)
    elif args.command == "logs":
        manager.logs(args.workstream, args.follow, args.lines)
    elif args.command == "run":
        manager.run(args.workstream, args.command)
    elif args.command == "status":
        manager.status(workstream_id=args.workstream, verbose=args.verbose)
    elif args.command == "cleanup":
        pass  # TODO'''

with open('workstreams.py', 'r') as f:
    content = f.read()

content = content.replace(old, new)

with open('workstreams.py', 'w') as f:
    f.write(content)

print("Fixed")
PYEOF
python3 fix_dispatch.py