import { useState } from 'react';
import DeleteProject from './DeleteProject';
import Link from 'next/link';
import {
  MoreHorizontal,
  ArrowUpRight,
  MessageSquare,
  SlidersHorizontal,
  Settings,
  Trash2,
} from 'lucide-react';
import Button from '@/components/ui/Button/Button';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuLabel,
} from '@/components/ui/shadcn/dropdown-menu';
export default function ProjectActions({
  id,
  name,
  onDeleted,
}: {
  id: string;
  name: string;
  onDeleted: () => void;
}) {
  const [deleting, setDeleting] = useState(false);
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Actions for ${name}`}
          >
            <MoreHorizontal size={18} />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-52">
          <DropdownMenuLabel>Project actions</DropdownMenuLabel>
          <DropdownMenuItem asChild>
            <Link href={`/project?projectId=${id}`}>
              <ArrowUpRight size={16} />
              Open project
            </Link>
          </DropdownMenuItem>
          <DropdownMenuItem asChild>
            <Link href={`/project?projectId=${id}`}>
              <MessageSquare size={16} />
              Project chat
            </Link>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem asChild>
            <Link href={{ pathname: '/runtime', query: { projectId: id } }}>
              <SlidersHorizontal size={16} />
              Runtime & requests
            </Link>
          </DropdownMenuItem>
          <DropdownMenuItem asChild>
            <Link href={{ pathname: '/settings', query: { projectId: id } }}>
              <Settings size={16} />
              Project settings
            </Link>
          </DropdownMenuItem>
          <DropdownMenuItem asChild>
            <Link href={`/runtime?projectId=${id}&tab=project`}>
              Export, history & maintenance
            </Link>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            variant="destructive"
            onSelect={() => setDeleting(true)}
          >
            <Trash2 size={16} />
            Remove project
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <DeleteProject
        id={id}
        name={name}
        open={deleting}
        onOpenChange={setDeleting}
        onDeleted={onDeleted}
      />
    </>
  );
}
