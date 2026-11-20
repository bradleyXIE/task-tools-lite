import { TaskShell } from "@/components/task/TaskShell";

export default async function TaskPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <TaskShell id={id} />;
}
