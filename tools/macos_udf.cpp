// DiskForge UDF 2.50 folder packer. Uses tsMuxer's Apache-2.0 IsoWriter.
#include "iso_writer.h"
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <vector>
#include <algorithm>
namespace fs = std::filesystem;
int main(int argc, char** argv) {
    try {
        if (argc != 4) throw std::runtime_error("Usage: discforge-udf SOURCE DESTINATION CAPACITY");
        const fs::path source = fs::canonical(argv[1]), dest = argv[2];
        const auto capacity = std::stoll(argv[3]);
        if (capacity <= 0 || fs::exists(dest)) throw std::runtime_error("Invalid capacity or destination exists");
        std::vector<fs::path> entries;
        int64_t bytes = 0;
        for (auto& entry : fs::recursive_directory_iterator(source)) {
            if (entry.is_symlink() || (!entry.is_directory() && !entry.is_regular_file()))
                throw std::runtime_error("Only regular files and directories are supported");
            const auto name = entry.path().filename().string();
            if (name.size() > 200 || std::any_of(name.begin(), name.end(), [](unsigned char c) { return c < 32 || c >= 127; }))
                throw std::runtime_error("Blu-ray internal filenames must be ASCII and at most 200 bytes");
            entries.push_back(entry.path());
            if (entry.is_regular_file()) bytes += entry.file_size();
        }
        if (bytes > capacity) throw std::runtime_error("Disc capacity exceeded");
        std::sort(entries.begin(), entries.end());
        IsoWriter writer(IsoHeaderData::normal());
        writer.setVolumeLabel("DISKFORGE");
        // Reserve enough metadata for each file and directory, including large collections.
        if (!writer.open(dest.string(), bytes, static_cast<int>(entries.size() / 8 + 2)))
            throw std::runtime_error("Cannot open output");
        std::vector<char> buffer(1024 * 1024);
        for (const auto& path : entries) {
            const auto name = fs::relative(path, source).generic_string();
            if (fs::is_directory(path)) { writer.createDir(name); continue; }
            std::ifstream input(path, std::ios::binary);
            if (!input) throw std::runtime_error("Cannot read " + path.string());
            std::unique_ptr<ISOFile> output(writer.createFile());
            if (!output->open(name.c_str(), AbstractStream::ofWrite)) throw std::runtime_error("Cannot create " + name);
            while (input) {
                input.read(buffer.data(), buffer.size());
                const auto count = input.gcount();
                if (count && output->write(buffer.data(), count) != count) throw std::runtime_error("ISO write failed");
            }
            if (!input.eof()) throw std::runtime_error("Source read failed");
            output->close();
        }
        writer.close();
        if (fs::file_size(dest) > static_cast<uint64_t>(capacity)) throw std::runtime_error("ISO exceeds disc capacity");
        std::cout << "UDF 2.50 image created\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
