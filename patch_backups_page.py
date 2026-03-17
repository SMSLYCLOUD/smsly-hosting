with open("frontend/src/app/backups/page.tsx", "r") as f:
    content = f.read()

search_block = """                                            {backup.status === 'COMPLETED' && (
                                                <>
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        onClick={() => handleRestore(backup.id)}
                                                        disabled={restoringId === backup.id}
                                                        title="Restore this backup"
                                                    >
                                                        {restoringId === backup.id
                                                            ? <Loader2 className="w-4 h-4 animate-spin" />
                                                            : <RotateCcw className="w-4 h-4" />}
                                                    </Button>
                                                    <Button variant="ghost" size="sm" asChild>
                                                        <a href={`/api/v1/server/backups/${backup.id}/download/`} target="_blank" rel="noopener noreferrer">
                                                            <Download className="w-4 h-4" />
                                                        </a>
                                                    </Button>
                                                </>
                                            )}"""

replace_block = """                                            {backup.status === 'COMPLETED' && (
                                                <>
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        onClick={() => handleRestore(backup.id)}
                                                        disabled={restoringId === backup.id}
                                                        title="Restore this backup"
                                                    >
                                                        {restoringId === backup.id
                                                            ? <Loader2 className="w-4 h-4 animate-spin" />
                                                            : <RotateCcw className="w-4 h-4" />}
                                                    </Button>
                                                    <Button variant="ghost" size="sm" asChild>
                                                        <a href={`/api/v1/server/backups/${backup.id}/download/`} target="_blank" rel="noopener noreferrer">
                                                            <Download className="w-4 h-4" />
                                                        </a>
                                                    </Button>
                                                    <Button variant="ghost" size="sm" onClick={() => handleDeleteBackup(backup.id)} title="Delete" className="text-red-400 hover:text-red-500">
                                                        <Trash2 className="w-4 h-4" />
                                                    </Button>
                                                </>
                                            )}"""

content = content.replace(search_block, replace_block)

with open("frontend/src/app/backups/page.tsx", "w") as f:
    f.write(content)
