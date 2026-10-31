"""Local fixes to pinned tsMuxer IsoWriter for large Blu-ray collections."""

def patch_source(source):
    for name in ('iso_writer.h', 'iso_writer.cpp'):
        path = source / 'tsMuxer' / name
        text = path.read_text()
        for old, new in [('uint8_t objectId', 'uint32_t objectId'), ('uint8_t m_objectId', 'uint32_t m_objectId'),
                         ('uint8_t m_objectUniqId', 'uint32_t m_objectUniqId'), ('uint8_t parentId', 'uint32_t parentId')]:
            text = text.replace(old, new)
        if name.endswith('.cpp'):
            replacements = [
                ('m_sectorsUsed = 2;  // restriction here: amount of files/subdirs is limited by sector size.',
                 '''{
            size_t bytes = 40; // Parent FID, followed by aligned ASCII identifiers.
            for (auto* entry : m_files) bytes += (38 + 1 + entry->m_name.size() + 3) & ~size_t(3);
            for (auto* entry : m_subDirs) bytes += (38 + 1 + entry->m_name.size() + 3) & ~size_t(3);
            m_sectorsUsed = 1 + (bytes + SECTOR_SIZE - 1) / SECTOR_SIZE;
        }'''),
                ('uint8_t buffer[SECTOR_SIZE] = {};', 'std::vector<uint8_t> buffer((m_sectorsUsed - 1) * SECTOR_SIZE, 0);'),
                ('writer.setBuffer(buffer, sizeof(buffer));', 'writer.setBuffer(buffer.data(), buffer.size());'),
                ('m_owner->absoluteSectorNum() + 1);', 'm_owner->absoluteSectorNum() + 1 + writer.size() / SECTOR_SIZE);'),
                ('assert(writer.size() < SECTOR_SIZE);  // not supported', 'assert(writer.size() <= static_cast<int64_t>(buffer.size()));'),
                ('m_owner->writeSector(buffer);', 'for (size_t at = 0; at < buffer.size(); at += SECTOR_SIZE) m_owner->writeSector(buffer.data() + at);'),
            ]
            for old, new in replacements:
                if old not in text:
                    raise RuntimeError('Pinned UDF source changed: ' + old)
                text = text.replace(old, new)
        path.write_text(text)
