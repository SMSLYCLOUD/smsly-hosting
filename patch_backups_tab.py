with open("frontend/src/components/settings/BackupsTab.tsx", "r") as f:
    content = f.read()

# Add the Delete button to the "COMPLETED" state block right next to the restore button.
# Let's find the TableCell containing the actions.

search_block = """                                            {backup.status === 'COMPLETED' && (
                                                <>
                                                    <Button variant="ghost" size="sm" onClick={() => handleRestore(backup.id)} disabled={restoringId === backup.id} title="Restore">
                                                        {restoringId === backup.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <RotateCcw className="w-4 h-4" />}
                                                    </Button>
                                                </>
                                            )}"""

replace_block = """                                            {backup.status === 'COMPLETED' && (
                                                <>
                                                    <Button variant="ghost" size="sm" onClick={() => handleRestore(backup.id)} disabled={restoringId === backup.id} title="Restore">
                                                        {restoringId === backup.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <RotateCcw className="w-4 h-4" />}
                                                    </Button>
                                                    <Button variant="ghost" size="sm" onClick={() => handleDeleteBackup(backup.id)} title="Delete" className="text-red-400 hover:text-red-500">
                                                        <Trash2 className="w-4 h-4" />
                                                    </Button>
                                                </>
                                            )}"""

content = content.replace(search_block, replace_block)

with open("frontend/src/components/settings/BackupsTab.tsx", "w") as f:
    f.write(content)
